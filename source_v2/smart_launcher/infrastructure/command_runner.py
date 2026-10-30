from __future__ import annotations

import codecs
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

from smart_launcher.domain.commands import (
    CommandFailure,
    CommandResult,
    CommandSpec,
    StreamChunk,
    StreamName,
)
from smart_launcher.infrastructure.process_supervisor import _WindowsJob

ChunkCallback = Callable[[StreamChunk], None]


class CommandRunner:
    """Runs bounded commands while preserving stdout and stderr separately."""

    def run(
        self,
        spec: CommandSpec,
        *,
        on_chunk: ChunkCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> CommandResult:
        started_at = datetime.now(UTC)
        process: subprocess.Popen[bytes] | None = None
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        sequence = 0
        sequence_lock = threading.Lock()
        last_activity = time.monotonic()

        def emit(stream: StreamName, text: str) -> None:
            nonlocal sequence, last_activity
            if not text:
                return
            last_activity = time.monotonic()
            target = stdout_parts if stream is StreamName.STDOUT else stderr_parts
            target.append(text)
            if on_chunk is None:
                return
            with sequence_lock:
                sequence += 1
                chunk = StreamChunk(
                    sequence=sequence,
                    source=spec.source,
                    stream=stream,
                    text=text,
                    timestamp=datetime.now(UTC),
                )
            try:
                on_chunk(chunk)
            except Exception:
                # UI/log callbacks must never terminate the child reader.
                pass

        job: _WindowsJob | None = None
        try:
            environment = os.environ.copy()
            environment.update(spec.env)
            kwargs: dict[str, object] = {
                "args": list(spec.argv),
                "cwd": str(spec.cwd) if spec.cwd else None,
                "env": environment,
                "stdin": subprocess.PIPE if spec.stdin is not None else subprocess.DEVNULL,
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
            if os.name == "nt":
                try:
                    job = _WindowsJob(process)
                except OSError:
                    job = None
        except FileNotFoundError as exc:
            return self._failed_result(spec, CommandFailure.NOT_FOUND, started_at, exc)
        except OSError as exc:
            return self._failed_result(spec, CommandFailure.START_FAILED, started_at, exc)

        assert process.stdout is not None
        assert process.stderr is not None

        def read_stream(pipe: object, stream: StreamName) -> None:
            decoder = codecs.getincrementaldecoder(spec.encoding)(errors="replace")
            read = getattr(pipe, "read1", pipe.read)  # type: ignore[attr-defined]
            while True:
                block = read(4096)
                if not block:
                    break
                emit(stream, decoder.decode(block))
            emit(stream, decoder.decode(b"", final=True))

        readers = [
            threading.Thread(
                target=read_stream,
                args=(process.stdout, StreamName.STDOUT),
                name="command-stdout",
                daemon=True,
            ),
            threading.Thread(
                target=read_stream,
                args=(process.stderr, StreamName.STDERR),
                name="command-stderr",
                daemon=True,
            ),
        ]
        for reader in readers:
            reader.start()

        if spec.stdin is not None and process.stdin is not None:
            try:
                process.stdin.write(spec.stdin)
                process.stdin.close()
            except BrokenPipeError:
                pass

        failure = CommandFailure.NONE
        deadline = time.monotonic() + spec.timeout if spec.timeout else None
        while process.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                failure = CommandFailure.CANCELLED
                self._terminate(process, job)
                break
            if deadline is not None and time.monotonic() >= deadline:
                failure = CommandFailure.TIMEOUT
                self._terminate(process, job)
                break
            if (
                spec.idle_timeout is not None
                and time.monotonic() - last_activity >= spec.idle_timeout
            ):
                failure = CommandFailure.TIMEOUT
                self._terminate(process, job)
                break
            time.sleep(0.025)

        process.wait()
        for reader in readers:
            reader.join(timeout=2.0)
        process.stdout.close()
        process.stderr.close()
        if job is not None:
            job.close()

        if failure is CommandFailure.NONE and process.returncode != 0:
            failure = CommandFailure.NONZERO_EXIT
        return CommandResult(
            spec=spec,
            failure=failure,
            return_code=process.returncode,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            stdout="".join(stdout_parts),
            stderr="".join(stderr_parts),
        )

    @staticmethod
    def _terminate(
        process: subprocess.Popen[bytes], job: _WindowsJob | None = None
    ) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGTERM)
            elif job is not None:
                job.terminate(1)
            else:
                process.terminate()
            process.wait(timeout=1.5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass

    @staticmethod
    def _failed_result(
        spec: CommandSpec,
        failure: CommandFailure,
        started_at: datetime,
        exc: OSError,
    ) -> CommandResult:
        return CommandResult(
            spec=spec,
            failure=failure,
            return_code=None,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            exception=str(exc),
        )
