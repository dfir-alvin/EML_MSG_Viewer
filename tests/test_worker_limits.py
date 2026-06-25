import sys
import unittest
from unittest.mock import patch

from viewer import load_worker
from viewer.security import ParseLimits


def _run_watchdog_synchronously(limits, rss_reader):
    """Invoke the watchdog's loop body on the current thread for testing.

    Replaces threading.Thread so start() runs the target inline, and converts
    the daemon's os._exit into a SystemExit we can catch.
    """

    class _InlineThread:
        def __init__(self, target, name, daemon):
            self._target = target

        def start(self):
            self._target()

    with patch.object(load_worker.threading, "Thread", _InlineThread):
        load_worker._start_memory_watchdog(limits, rss_reader=rss_reader)


class WorkerMemoryLimitTests(unittest.TestCase):
    def test_watchdog_terminates_when_rss_exceeds_limit(self):
        limits = ParseLimits(worker_memory_bytes=100)
        exits = []

        with (
            patch.object(load_worker.os, "_exit", side_effect=lambda code: (exits.append(code), (_ for _ in ()).throw(SystemExit()))),
            patch.object(load_worker.time, "sleep", side_effect=AssertionError("should exit before sleeping")),
        ):
            with self.assertRaises(SystemExit):
                _run_watchdog_synchronously(limits, rss_reader=lambda: 1_000)

        self.assertEqual(exits, [137])

    def test_watchdog_polls_while_within_limit(self):
        limits = ParseLimits(worker_memory_bytes=10_000)
        readings = iter([100, 200])
        sleeps = []

        def reader():
            try:
                return next(readings)
            except StopIteration:
                raise SystemExit()  # break the otherwise-infinite loop

        with (
            patch.object(load_worker.os, "_exit", side_effect=AssertionError("must not exit")),
            patch.object(load_worker.time, "sleep", side_effect=lambda s: sleeps.append(s)),
        ):
            with self.assertRaises(SystemExit):
                _run_watchdog_synchronously(limits, rss_reader=reader)

        self.assertEqual(sleeps, [0.25, 0.25])

    @unittest.skipUnless(sys.platform.startswith("win"), "Windows Job Object path")
    def test_windows_job_object_limit_installs_hard_cap(self):
        limits = ParseLimits(worker_memory_bytes=512 * 1024 * 1024)
        installed = load_worker._apply_windows_memory_limit(limits)
        # The call must fail closed (False) or succeed and retain the handle.
        if installed:
            self.assertIsNotNone(load_worker._WINDOWS_JOB_HANDLE)

    def test_apply_process_limits_starts_watchdog_everywhere(self):
        limits = ParseLimits()
        with (
            patch.object(load_worker, "_start_memory_watchdog") as watchdog,
            patch.object(load_worker, "_apply_posix_memory_limit"),
            patch.object(load_worker, "_apply_windows_memory_limit"),
        ):
            load_worker._apply_process_limits(limits)
        watchdog.assert_called_once_with(limits)


if __name__ == "__main__":
    unittest.main()
