from __future__ import annotations

import sys
import threading
import time
import unittest

from smart_launcher.domain.commands import CommandFailure, CommandSpec, StreamName
from smart_launcher.infrastructure.command_runner import CommandRunner


class CommandRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CommandRunner()

    def test_captures_stdout_stderr_and_exit_code(self) -> None:
        chunks = []
        result = self.runner.run(
            CommandSpec(
                argv=(
                    sys.executable,
                    "-c",
                    "import sys; print('out'); print('err', file=sys.stderr)",
                ),
                source="test",
            ),
            on_chunk=chunks.append,
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(result.return_code, 0)
        self.assertIn("out", result.stdout)
        self.assertIn("err", result.stderr)
        self.assertEqual({chunk.stream for chunk in chunks}, {StreamName.STDOUT, StreamName.STDERR})
        self.assertEqual([chunk.sequence for chunk in chunks], list(range(1, len(chunks) + 1)))

    def test_reports_nonzero_exit(self) -> None:
        result = self.runner.run(
            CommandSpec(argv=(sys.executable, "-c", "raise SystemExit(7)"))
        )

        self.assertEqual(result.failure, CommandFailure.NONZERO_EXIT)
        self.assertEqual(result.return_code, 7)

    def test_reports_missing_executable(self) -> None:
        result = self.runner.run(CommandSpec(argv=("missing-smart-launcher-command",)))

        self.assertEqual(result.failure, CommandFailure.NOT_FOUND)
        self.assertIsNone(result.return_code)

    def test_timeout_stops_command(self) -> None:
        result = self.runner.run(
            CommandSpec(
                argv=(sys.executable, "-c", "import time; time.sleep(30)"),
                timeout=0.15,
            )
        )

        self.assertEqual(result.failure, CommandFailure.TIMEOUT)
        self.assertLess(result.duration_seconds, 3.0)

    def test_idle_timeout_stops_command_after_output_becomes_silent(self) -> None:
        result = self.runner.run(
            CommandSpec(
                argv=(
                    sys.executable,
                    "-c",
                    "import time; print('started', flush=True); time.sleep(30)",
                ),
                timeout=10,
                idle_timeout=0.15,
            )
        )

        self.assertEqual(result.failure, CommandFailure.TIMEOUT)
        self.assertIn("started", result.stdout)
        self.assertLess(result.duration_seconds, 3.0)

    @unittest.skipUnless(sys.platform == "win32", "Windows Job Object behavior")
    def test_cancel_terminates_child_process_tree(self) -> None:
        cancel = threading.Event()
        child_pid: list[int] = []

        def receive(chunk) -> None:
            if "CHILD=" in chunk.text:
                child_pid.append(int(chunk.text.split("CHILD=", 1)[1].splitlines()[0]))
                cancel.set()

        script = (
            "import subprocess,sys,time; "
            "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
            "print(f'CHILD={p.pid}',flush=True); time.sleep(30)"
        )
        result = self.runner.run(
            CommandSpec(argv=(sys.executable, "-c", script)),
            on_chunk=receive,
            cancel_event=cancel,
        )

        self.assertEqual(result.failure, CommandFailure.CANCELLED)
        self.assertTrue(child_pid)
        time.sleep(0.1)
        self.assertFalse(_windows_process_active(child_pid[0]))

    def test_cancel_event_stops_command(self) -> None:
        cancel = threading.Event()
        timer = threading.Timer(0.1, cancel.set)
        timer.start()
        try:
            result = self.runner.run(
                CommandSpec(argv=(sys.executable, "-c", "import time; time.sleep(30)")),
                cancel_event=cancel,
            )
        finally:
            timer.cancel()

        self.assertEqual(result.failure, CommandFailure.CANCELLED)

    def test_stream_callback_receives_short_line_before_process_exit(self) -> None:
        received = threading.Event()
        finished = threading.Event()

        def run() -> None:
            self.runner.run(
                CommandSpec(
                    argv=(
                        sys.executable,
                        "-c",
                        "import time; print('ready', flush=True); time.sleep(2)",
                    )
                ),
                on_chunk=lambda chunk: received.set() if "ready" in chunk.text else None,
            )
            finished.set()

        worker = threading.Thread(target=run)
        worker.start()
        try:
            self.assertTrue(received.wait(1.0))
            self.assertFalse(finished.is_set())
        finally:
            worker.join(4.0)


def _windows_process_active(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    process = kernel32.OpenProcess(0x1000, False, pid)
    if not process:
        return False
    try:
        exit_code = wintypes.DWORD()
        return bool(
            kernel32.GetExitCodeProcess(process, ctypes.byref(exit_code))
            and exit_code.value == 259
        )
    finally:
        kernel32.CloseHandle(process)


if __name__ == "__main__":
    unittest.main()
