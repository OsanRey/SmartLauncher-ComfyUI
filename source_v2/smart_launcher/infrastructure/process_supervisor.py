from __future__ import annotations

import codecs
import ctypes
import os
import signal
import subprocess
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from smart_launcher.domain.commands import CommandSpec, StreamChunk, StreamName
from smart_launcher.domain.processes import ProcessExit, ProcessSession, ProcessState

ChunkCallback = Callable[[StreamChunk], None]
ExitCallback = Callable[[ProcessExit], None]


class ProcessSupervisor:
    """Owns one long-running process and, where possible, its process tree."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._process: subprocess.Popen[bytes] | None = None
        self._session: ProcessSession | None = None
        self._job: _WindowsJob | None = None
        self._stop_requested = False
        self._watcher: threading.Thread | None = None

    @property
    def session(self) -> ProcessSession | None:
        with self._lock:
            return self._session

    def start(
        self,
        spec: CommandSpec,
        *,
        on_chunk: ChunkCallback | None = None,
        on_exit: ExitCallback | None = None,
    ) -> ProcessSession:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                raise RuntimeError("A managed process is already running.")

            environment = os.environ.copy()
            environment.update(spec.env)
            kwargs: dict[str, object] = {
                "args": list(spec.argv),
                "cwd": str(spec.cwd) if spec.cwd else None,
                "env": environment,
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "shell": False,
            }
            if os.name == "nt":
                flags = subprocess.CREATE_NEW_PROCESS_GROUP
                if spec.hide_window:
                    flags |= subprocess.CREATE_NO_WINDOW
                kwargs["creationflags"] = flags
            else:
                kwargs["start_new_session"] = True

            process = subprocess.Popen(**kwargs)  # type: ignore[arg-type]
            job: _WindowsJob | None = None
            if os.name == "nt":
                try:
                    job = _WindowsJob(process)
                except OSError:
                    job = None

            started_at = datetime.now(UTC)
            session = ProcessSession(
                session_id=uuid.uuid4().hex,
                pid=process.pid,
                state=ProcessState.RUNNING,
                started_at=started_at,
                tree_controlled=job is not None,
            )
            self._process = process
            self._session = session
            self._job = job
            self._stop_requested = False

            watcher = threading.Thread(
                target=self._watch,
                args=(process, session, spec, on_chunk, on_exit),
                name=f"process-supervisor-{process.pid}",
                daemon=True,
            )
            self._watcher = watcher
            watcher.start()
            return session

    def stop(self, *, graceful_timeout: float = 2.0, force_timeout: float = 3.0) -> bool:
        if graceful_timeout < 0 or force_timeout < 0:
            raise ValueError("Stop timeouts cannot be negative.")
        with self._lock:
            process = self._process
            session = self._session
            job = self._job
            watcher = self._watcher
            if process is None:
                return False
            root_running = process.poll() is None
            tree_running = watcher is not None and watcher.is_alive()
            if not root_running and (job is None or not tree_running):
                return False
            self._stop_requested = True
            if session is not None:
                self._session = ProcessSession(
                    session.session_id,
                    session.pid,
                    ProcessState.STOPPING,
                    session.started_at,
                    session.tree_controlled,
                )

        if root_running:
            self._request_graceful_stop(process)
            try:
                process.wait(timeout=graceful_timeout)
            except subprocess.TimeoutExpired:
                if job is not None:
                    job.terminate(1)
                else:
                    try:
                        process.terminate()
                    except OSError:
                        pass
                try:
                    process.wait(timeout=force_timeout)
                except subprocess.TimeoutExpired:
                    if job is None:
                        try:
                            process.kill()
                        except OSError:
                            pass
                        process.wait()
        if job is not None:
            # The original PID may already have exited after spawning a replacement
            # process (ComfyUI Manager's browser-triggered restart). Stop the whole
            # owned tree even when there is no longer a root process to signal.
            job.terminate(1)
        if watcher is not None and watcher is not threading.current_thread():
            watcher.join(timeout=max(1.0, force_timeout + 1.0))
        return True

    def _watch(
        self,
        process: subprocess.Popen[bytes],
        session: ProcessSession,
        spec: CommandSpec,
        on_chunk: ChunkCallback | None,
        on_exit: ExitCallback | None,
    ) -> None:
        assert process.stdout is not None
        assert process.stderr is not None
        sequence = 0
        sequence_lock = threading.Lock()

        def read_stream(pipe: object, stream: StreamName) -> None:
            nonlocal sequence
            decoder = codecs.getincrementaldecoder(spec.encoding)(errors="replace")
            read = getattr(pipe, "read1", pipe.read)  # type: ignore[attr-defined]
            while True:
                block = read(4096)
                if not block:
                    break
                text = decoder.decode(block)
                if text and on_chunk is not None:
                    with sequence_lock:
                        sequence += 1
                        chunk = StreamChunk(
                            sequence,
                            spec.source,
                            stream,
                            text,
                            datetime.now(UTC),
                        )
                    try:
                        on_chunk(chunk)
                    except Exception:
                        pass
            tail = decoder.decode(b"", final=True)
            if tail and on_chunk is not None:
                with sequence_lock:
                    sequence += 1
                    chunk = StreamChunk(
                        sequence,
                        spec.source,
                        stream,
                        tail,
                        datetime.now(UTC),
                    )
                try:
                    on_chunk(chunk)
                except Exception:
                    pass

        readers = [
            threading.Thread(
                target=read_stream,
                args=(process.stdout, StreamName.STDOUT),
                name=f"managed-stdout-{process.pid}",
                daemon=True,
            ),
            threading.Thread(
                target=read_stream,
                args=(process.stderr, StreamName.STDERR),
                name=f"managed-stderr-{process.pid}",
                daemon=True,
            ),
        ]
        for reader in readers:
            reader.start()
        return_code = process.wait()
        for reader in readers:
            # A self-restarted server inherits these pipes. EOF therefore marks the
            # end of the owned process tree, not merely the original PID.
            reader.join()
        process.stdout.close()
        process.stderr.close()

        with self._lock:
            requested = self._stop_requested
            job = self._job if self._process is process else None
            if self._process is process:
                self._process = None
                self._job = None
                self._watcher = None
                self._session = ProcessSession(
                    session.session_id,
                    session.pid,
                    ProcessState.STOPPED if requested or return_code == 0 else ProcessState.FAILED,
                    session.started_at,
                    session.tree_controlled,
                )
        if job is not None:
            job.close()
        event = ProcessExit(
            session.session_id,
            session.pid,
            return_code,
            session.started_at,
            datetime.now(UTC),
            requested,
        )
        if on_exit is not None:
            try:
                on_exit(event)
            except Exception:
                pass

    @staticmethod
    def _request_graceful_stop(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "nt":
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(process.pid, signal.SIGTERM)
        except OSError:
            pass


if os.name == "nt":
    from ctypes import wintypes

    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

    class _BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.CloseHandle.restype = wintypes.BOOL


class _WindowsJob:
    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        if os.name != "nt":
            raise OSError("Windows Job Objects are unavailable.")
        handle = _kernel32.CreateJobObjectW(None, None)  # type: ignore[name-defined]
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._handle = handle
        information = _ExtendedLimitInformation()  # type: ignore[name-defined]
        information.BasicLimitInformation.LimitFlags = (  # type: ignore[name-defined]
            _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        configured = _kernel32.SetInformationJobObject(  # type: ignore[name-defined]
            handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,  # type: ignore[name-defined]
            ctypes.byref(information),
            ctypes.sizeof(information),
        )
        assigned = configured and _kernel32.AssignProcessToJobObject(  # type: ignore[name-defined]
            handle, process._handle  # type: ignore[attr-defined]
        )
        if not assigned:
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)

    def terminate(self, exit_code: int) -> None:
        if self._handle:
            _kernel32.TerminateJobObject(self._handle, exit_code)  # type: ignore[name-defined]

    def close(self) -> None:
        if self._handle:
            _kernel32.CloseHandle(self._handle)  # type: ignore[name-defined]
            self._handle = None

    def __del__(self) -> None:
        self.close()
