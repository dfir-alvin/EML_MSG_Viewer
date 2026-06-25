"""Qt thread that supervises the disposable email-processing process."""

from __future__ import annotations

import threading
import time

from PyQt6.QtCore import QThread, pyqtSignal

from viewer.load_worker import new_worker_process, process_rss, terminate_process
from viewer.security import NetworkMode, ParseLimits, RemoteFetchPolicy


class EmailLoadThread(QThread):
    loaded = pyqtSignal(object)
    failed = pyqtSignal(str)
    phase_changed = pyqtSignal(str)

    def __init__(
        self,
        path: str,
        limits: ParseLimits,
        network_mode: NetworkMode,
        remote_policy: RemoteFetchPolicy,
        parent=None,
    ):
        super().__init__(parent)
        self._path = path
        self._limits = limits
        self._network_mode = network_mode
        self._remote_policy = remote_policy
        self._cancelled = threading.Event()
        self._process = None

    def cancel(self) -> None:
        self._cancelled.set()
        if self._process is not None:
            terminate_process(self._process)

    def run(self) -> None:
        connection = None
        try:
            process, connection = new_worker_process(
                self._path,
                self._limits,
                self._network_mode,
                self._remote_policy,
            )
            self._process = process
            deadline = time.monotonic() + self._limits.parse_timeout_seconds
            phase = "parse"

            while True:
                if self._cancelled.is_set():
                    terminate_process(process)
                    return
                if time.monotonic() > deadline:
                    terminate_process(process)
                    label = "Remote image loading" if phase == "fetch" else "Email processing"
                    self.failed.emit(f"{label} exceeded its safe time limit")
                    return

                rss = process_rss(process.pid) if process.pid else None
                if rss is not None and rss > self._limits.worker_memory_bytes:
                    terminate_process(process)
                    self.failed.emit("Email processing exceeded the memory limit")
                    return

                if connection.poll(0.1):
                    message = connection.recv()
                    kind = message[0]
                    if kind == "phase":
                        phase = message[1]
                        if phase == "fetch":
                            deadline = time.monotonic() + self._remote_policy.total_timeout_seconds
                            self.phase_changed.emit("Loading restricted remote images…")
                        continue
                    if kind == "result":
                        process.join(timeout=2.0)
                        self.loaded.emit(message[1])
                        return
                    if kind == "error":
                        process.join(timeout=2.0)
                        self.failed.emit(message[2])
                        return

                if not process.is_alive():
                    if connection.poll():
                        continue
                    process.join(timeout=0.2)
                    self.failed.emit("The email security worker exited unexpectedly")
                    return
        except (EOFError, OSError) as exc:
            if not self._cancelled.is_set():
                self.failed.emit(f"The email security worker failed: {exc}")
        finally:
            if connection is not None:
                connection.close()
            if self._process is not None and self._process.is_alive():
                terminate_process(self._process)
            self._process = None

