from __future__ import annotations

import threading
import tempfile
from collections.abc import Iterable
from pathlib import Path

from smart_launcher.domain.commands import CommandResult, CommandSpec
from smart_launcher.domain.maintenance import DependencySyncResult
from smart_launcher.infrastructure.command_runner import ChunkCallback, CommandRunner
from smart_launcher.runtime.package_integrity import PackageIntegrity


class DependencySynchronizer:
    """Runs explicit pip transactions; it never decides when an update is needed."""

    def __init__(self, runner: CommandRunner | None = None) -> None:
        self._runner = runner or CommandRunner()

    def sync(
        self,
        python: Path,
        manifests: Iterable[Path],
        *,
        torch_packages: tuple[str, ...] = (),
        torch_index_url: str | None = None,
        upgrade: bool = True,
        on_chunk: ChunkCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> DependencySyncResult:
        executable = python.resolve()
        selected = tuple(dict.fromkeys(Path(path).resolve() for path in manifests))
        missing = tuple(path for path in selected if not path.is_file())
        if missing:
            return DependencySyncResult(
                False,
                (),
                "Dependency manifest is missing: " + ", ".join(str(path) for path in missing),
            )

        installed: list[Path] = []
        if torch_packages:
            arguments = ["install", "--upgrade", *torch_packages]
            if torch_index_url:
                arguments.extend(("--index-url", torch_index_url))
            result = self._pip(
                executable,
                *arguments,
                source="pip-torch",
                on_chunk=on_chunk,
                cancel_event=cancel_event,
            )
            if not result.succeeded:
                return DependencySyncResult(False, (), self._detail(result))

        for manifest in selected:
            arguments = ["install"]
            if upgrade:
                arguments.append("--upgrade")
            arguments.extend(("-r", str(manifest)))
            result = self._pip(
                executable,
                *arguments,
                source="pip-requirements",
                on_chunk=on_chunk,
                cancel_event=cancel_event,
            )
            if not result.succeeded:
                return DependencySyncResult(False, tuple(installed), self._detail(result))
            installed.append(manifest)

        checked = self._pip(
            executable,
            "check",
            source="pip-check",
            on_chunk=on_chunk,
            cancel_event=cancel_event,
        )
        if not checked.succeeded:
            return DependencySyncResult(False, tuple(installed), self._detail(checked))
        return DependencySyncResult(
            True,
            tuple(installed),
            checked.stdout.strip() or "Dependencies synchronized and pip check passed.",
        )

    def _pip(
        self,
        python: Path,
        *arguments: str,
        source: str,
        on_chunk: ChunkCallback | None,
        cancel_event: threading.Event | None,
    ) -> CommandResult:
        if arguments and arguments[0] == "install":
            with tempfile.TemporaryDirectory(prefix="launcher-pip-report-") as folder:
                report = Path(folder) / "report.json"
                result = self._pip_command(
                    python, *arguments, "--report", str(report), source=source,
                    on_chunk=on_chunk, cancel_event=cancel_event,
                )
                if not result.succeeded:
                    return result
                return PackageIntegrity(self._runner).verify(
                    python, report, cancel_event=cancel_event, on_chunk=on_chunk,
                )
        return self._pip_command(python, *arguments, source=source,
                                 on_chunk=on_chunk, cancel_event=cancel_event)

    def _pip_command(
        self, python: Path, *arguments: str, source: str,
        on_chunk: ChunkCallback | None, cancel_event: threading.Event | None,
    ) -> CommandResult:
        return self._runner.run(
            CommandSpec(
                argv=(str(python), "-I", "-m", "pip", *arguments),
                env={"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
                source=source,
                timeout=3600.0,
            ),
            on_chunk=on_chunk,
            cancel_event=cancel_event,
        )

    @staticmethod
    def _detail(result: CommandResult) -> str:
        detail = result.exception or result.stderr.strip() or result.stdout.strip() or result.failure.value
        if result.spec.source == "package-integrity" and not result.succeeded:
            return "Проверка целостности установленных пакетов не пройдена: " + detail
        return detail
