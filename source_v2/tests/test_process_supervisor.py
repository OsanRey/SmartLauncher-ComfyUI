from __future__ import annotations

import sys
import threading
import time
import unittest
import os

from smart_launcher.domain.commands import CommandSpec
from smart_launcher.domain.processes import ProcessState
from smart_launcher.infrastructure.process_supervisor import ProcessSupervisor


class ProcessSupervisorTests(unittest.TestCase):
    def test_streams_output_and_reports_natural_exit(self) -> None:
        supervisor = ProcessSupervisor()
        chunks = []
        exited = threading.Event()

        session = supervisor.start(
            CommandSpec(
                argv=(sys.executable, "-c", "print('ready'); print('done')"),
                source="comfyui",
            ),
            on_chunk=chunks.append,
            on_exit=lambda _event: exited.set(),
        )

        self.assertTrue(exited.wait(5.0))
        self.assertIn("ready", "".join(chunk.text for chunk in chunks))
        self.assertEqual(supervisor.session.state, ProcessState.STOPPED)  # type: ignore[union-attr]
        self.assertGreater(session.pid, 0)

    def test_refuses_second_owned_process(self) -> None:
        supervisor = ProcessSupervisor()
        supervisor.start(CommandSpec(argv=(sys.executable, "-c", "import time; time.sleep(30)")))
        try:
            with self.assertRaises(RuntimeError):
                supervisor.start(CommandSpec(argv=(sys.executable, "-c", "print('second')")))
        finally:
            supervisor.stop(graceful_timeout=0.1)

    def test_stop_marks_exit_as_requested(self) -> None:
        supervisor = ProcessSupervisor()
        exits = []
        exited = threading.Event()
        supervisor.start(
            CommandSpec(argv=(sys.executable, "-c", "import time; time.sleep(30)")),
            on_exit=lambda event: (exits.append(event), exited.set()),
        )
        time.sleep(0.1)

        stopped = supervisor.stop(graceful_timeout=0.1)

        self.assertTrue(stopped)
        self.assertTrue(exited.wait(3.0))
        self.assertTrue(exits[0].stop_requested)
        self.assertEqual(supervisor.session.state, ProcessState.STOPPED)  # type: ignore[union-attr]

    @unittest.skipUnless(os.name == "nt", "Windows Job Object behavior")
    def test_stop_terminates_owned_child_tree(self) -> None:
        supervisor = ProcessSupervisor()
        output = []
        received_pid = threading.Event()

        def receive(chunk) -> None:
            output.append(chunk.text)
            if "CHILD=" in "".join(output):
                received_pid.set()

        script = (
            "import subprocess,sys,time; "
            "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
            "print(f'CHILD={p.pid}',flush=True); time.sleep(30)"
        )
        session = supervisor.start(
            CommandSpec(argv=(sys.executable, "-c", script)), on_chunk=receive
        )
        if not session.tree_controlled:
            supervisor.stop(graceful_timeout=0.1)
            self.skipTest("Nested Windows Job Objects are unavailable")
        self.assertTrue(received_pid.wait(5.0))
        child_pid = int("".join(output).split("CHILD=", 1)[1].splitlines()[0])

        supervisor.stop(graceful_timeout=0.1)

        self.assertFalse(_windows_process_active(child_pid))

    @unittest.skipUnless(os.name == "nt", "Windows Job Object behavior")
    def test_parent_exit_keeps_self_restarted_child_session_alive(self) -> None:
        supervisor = ProcessSupervisor()
        output = []
        replacement_ready = threading.Event()
        exited = threading.Event()

        def receive(chunk) -> None:
            output.append(chunk.text)
            if "REPLACEMENT_READY" in "".join(output):
                replacement_ready.set()

        child = "import time; print('REPLACEMENT_READY', flush=True); time.sleep(30)"
        parent = (
            "import subprocess,sys; "
            f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
            "print('PARENT_EXITING', flush=True)"
        )
        session = supervisor.start(
            CommandSpec(argv=(sys.executable, "-c", parent)),
            on_chunk=receive,
            on_exit=lambda _event: exited.set(),
        )
        if not session.tree_controlled:
            supervisor.stop(graceful_timeout=0.1)
            self.skipTest("Nested Windows Job Objects are unavailable")

        self.assertTrue(replacement_ready.wait(5.0))
        self.assertFalse(exited.wait(0.25))
        self.assertEqual(supervisor.session.state, ProcessState.RUNNING)  # type: ignore[union-attr]

        self.assertTrue(supervisor.stop(graceful_timeout=0.1))
        self.assertTrue(exited.wait(3.0))
        self.assertEqual(supervisor.session.state, ProcessState.STOPPED)  # type: ignore[union-attr]


def _windows_process_active(pid: int) -> bool:
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    process = kernel32.OpenProcess(0x1000, False, pid)
    if not process:
        return False
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(process, ctypes.byref(exit_code)):
            return False
        return exit_code.value == 259
    finally:
        kernel32.CloseHandle(process)


if __name__ == "__main__":
    unittest.main()
