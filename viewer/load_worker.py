"""Out-of-process email parsing, sanitization, and remote-image fetching."""

from __future__ import annotations

import multiprocessing
import os
import threading
import time
from dataclasses import dataclass
from multiprocessing.connection import Connection

from viewer.email_parser import ParsedEmail, parse_email_file
from viewer.remote_fetch import fetch_remote_images
from viewer.sanitizer import sanitize_html, text_to_html
from viewer.security import (
    BLANK_PNG_BYTES,
    InlineAsset,
    NetworkMode,
    ParseLimits,
    RemoteFetchPolicy,
    RemoteImageFailure,
    SecurityLimitError,
)


@dataclass
class LoadedEmail:
    parsed: ParsedEmail
    rendered_html: str
    assets: dict[str, InlineAsset]
    remote_errors: tuple[RemoteImageFailure, ...] = ()


# A Windows Job Object handle must outlive the call that creates it, otherwise
# closing the handle releases the limit. Hold it at module scope for the life
# of the worker process.
_WINDOWS_JOB_HANDLE = None


def _apply_posix_memory_limit(limits: ParseLimits) -> None:
    try:
        import resource

        resource.setrlimit(
            resource.RLIMIT_AS,
            (limits.worker_memory_bytes, limits.worker_memory_bytes),
        )
        cpu_seconds = max(1, int(limits.parse_timeout_seconds) + 1)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
    except (ImportError, OSError, ValueError):
        # The parent still enforces wall-clock and RSS limits, and the watchdog
        # below provides an in-process backstop.
        pass


def _apply_windows_memory_limit(limits: ParseLimits) -> bool:
    """Assign this process to a Job Object with a hard process-memory cap.

    Returns True if the hard limit was installed. Uses only ctypes/stdlib so no
    new dependency is introduced. Nested jobs are supported on Windows 8+
    (including inside Windows Sandbox), so self-assignment normally succeeds.
    """
    global _WINDOWS_JOB_HANDLE
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        JobObjectExtendedLimitInformation = 9
        JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.POINTER(wintypes.ULONG)),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return False

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_PROCESS_MEMORY
        info.ProcessMemoryLimit = ctypes.c_size_t(limits.worker_memory_bytes)
        if not kernel32.SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            kernel32.CloseHandle(job)
            return False

        if not kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
            kernel32.CloseHandle(job)
            return False

        _WINDOWS_JOB_HANDLE = job
        return True
    except Exception:
        return False


def _start_memory_watchdog(limits: ParseLimits, rss_reader=None) -> None:
    """Self-terminate if RSS exceeds the cap, independent of the parent poll.

    This is defense-in-depth: it works even where the OS-level hard cap could
    not be installed, and reacts faster than the parent's RSS poll. rss_reader
    is injectable for testing.
    """
    reader = rss_reader or (lambda: process_rss(os.getpid()))
    limit = limits.worker_memory_bytes

    def watch() -> None:
        while True:
            rss = reader()
            if rss is not None and rss > limit:
                # Hard, immediate exit; the parent maps this to a failure.
                os._exit(137)
            time.sleep(0.25)

    thread = threading.Thread(target=watch, name="memory-watchdog", daemon=True)
    thread.start()


def _apply_process_limits(limits: ParseLimits) -> None:
    if os.name == "posix":
        _apply_posix_memory_limit(limits)
    elif os.name == "nt":
        _apply_windows_memory_limit(limits)
    # The watchdog runs everywhere as an in-process backstop.
    _start_memory_watchdog(limits)


def process_email(
    path: str,
    limits: ParseLimits,
    network_mode: NetworkMode,
    remote_policy: RemoteFetchPolicy,
    phase_callback=None,
) -> LoadedEmail:
    parsed = parse_email_file(path, limits)
    if parsed.html_body:
        sanitized = sanitize_html(
            parsed.html_body,
            parsed.inline_images,
            network_mode,
            remote_policy,
        )
        rendered_html = sanitized.html
    elif parsed.text_body:
        sanitized = None
        rendered_html = text_to_html(parsed.text_body)
    else:
        sanitized = None
        rendered_html = "<html><body><p style='color:gray;'>(No message body)</p></body></html>"

    rendered_size = len(rendered_html.encode("utf-8", errors="replace"))
    if rendered_size > limits.max_body_bytes:
        raise SecurityLimitError(
            "rendered_body_size",
            "Sanitized message body exceeds the safe display limit",
        )

    assets = dict(parsed.inline_images)
    remote_errors: tuple[RemoteImageFailure, ...] = ()
    if sanitized and sanitized.remote_images and network_mode is NetworkMode.RESTRICTED_REMOTE_IMAGES:
        if phase_callback:
            phase_callback("fetch")
        fetched, remote_errors = fetch_remote_images(sanitized.remote_images, remote_policy)
        assets.update(fetched)
        blank = InlineAsset(data=BLANK_PNG_BYTES, mime_type="image/png")
        for reference in sanitized.remote_images:
            assets.setdefault(reference.token, blank)

    # Bodies are no longer needed after rendering and can be large when sent
    # through the multiprocessing pipe.
    parsed.html_body = ""
    parsed.text_body = ""
    return LoadedEmail(
        parsed=parsed,
        rendered_html=rendered_html,
        assets=assets,
        remote_errors=remote_errors,
    )


def worker_entry(
    send_connection: Connection,
    path: str,
    limits: ParseLimits,
    network_mode: NetworkMode,
    remote_policy: RemoteFetchPolicy,
) -> None:
    _apply_process_limits(limits)

    def phase(name: str) -> None:
        send_connection.send(("phase", name))

    try:
        loaded = process_email(path, limits, network_mode, remote_policy, phase)
        send_connection.send(("result", loaded))
    except SecurityLimitError as exc:
        send_connection.send(("error", exc.code, str(exc)))
    except MemoryError:
        send_connection.send(("error", "memory_limit", "Email processing exceeded the memory limit"))
    except BaseException as exc:
        send_connection.send(("error", "parse_error", f"Could not safely process this email: {exc}"))
    finally:
        send_connection.close()


def terminate_process(process: multiprocessing.Process) -> None:
    if not process.is_alive():
        process.join(timeout=0.2)
        return
    process.terminate()
    process.join(timeout=2.0)
    if process.is_alive() and hasattr(process, "kill"):
        process.kill()
        process.join(timeout=1.0)


def process_rss(process_id: int) -> int | None:
    try:
        import psutil

        process = psutil.Process(process_id)
        total = process.memory_info().rss
        for child in process.children(recursive=True):
            try:
                total += child.memory_info().rss
            except psutil.Error:
                pass
        return total
    except Exception:
        return None


def new_worker_process(
    path: str,
    limits: ParseLimits,
    network_mode: NetworkMode,
    remote_policy: RemoteFetchPolicy,
):
    context = multiprocessing.get_context("spawn")
    receive_connection, send_connection = context.Pipe(duplex=False)
    process = context.Process(
        target=worker_entry,
        args=(send_connection, path, limits, network_mode, remote_policy),
        name="email-security-worker",
        daemon=True,
    )
    try:
        process.start()
    except Exception:
        receive_connection.close()
        send_connection.close()
        raise
    send_connection.close()
    return process, receive_connection
