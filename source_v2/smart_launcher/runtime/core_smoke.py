from __future__ import annotations

import threading
from pathlib import Path

from smart_launcher.domain.commands import CommandSpec
from smart_launcher.infrastructure.command_runner import ChunkCallback, CommandRunner


class ComfyCoreSmokeValidator:
    """Starts ComfyUI just far enough to validate core imports and initialization."""

    def __init__(self, runner: CommandRunner | None = None) -> None:
        self._runner = runner or CommandRunner()

    def validate(
        self,
        python: Path,
        root: Path,
        *,
        on_chunk: ChunkCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> tuple[bool, str]:
        main = root / "main.py"
        if not main.is_file():
            return False, f"ComfyUI entry point is missing: {main}"
        # ComfyUI emits its complete startup log during this probe. Keep it captured
        # for failure diagnostics instead of making a healthy check look like a launch.
        result = self._runner.run(
            CommandSpec(
                argv=(
                    str(python.resolve()),
                    str(main.resolve()),
                    "--quick-test-for-ci",
                    "--disable-all-custom-nodes",
                    "--disable-api-nodes",
                ),
                cwd=root.resolve(),
                env={"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
                source="comfy-smoke",
                timeout=180.0,
                idle_timeout=90.0,
            ),
            cancel_event=cancel_event,
        )
        if result.succeeded:
            return True, "ComfyUI core startup check passed."
        detail = (
            result.exception
            or result.stderr.strip()
            or result.stdout.strip()
            or result.failure.value
        )
        return False, f"ComfyUI core startup check failed: {detail}"
