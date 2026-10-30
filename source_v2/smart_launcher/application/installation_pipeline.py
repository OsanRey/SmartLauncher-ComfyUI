from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import threading
import uuid
from pathlib import Path

from smart_launcher.application.environment_service import ComfyEnvironmentService
from smart_launcher.comfy.repository import ComfyRepository, RepositoryError
from smart_launcher.domain.commands import CommandSpec
from smart_launcher.domain.dependencies import TorchExpectation
from smart_launcher.domain.installation import (
    InstallationRequest,
    InstallationResult,
    InstallationStage,
)
from smart_launcher.domain.runtimes import RuntimeProbe
from smart_launcher.infrastructure.command_runner import ChunkCallback, CommandRunner


class InstallationPipeline:
    MARKER = ".smart-launcher-installing.json"
    CUDA_MINIMUM_FREE_BYTES = 12 * 1024**3
    CPU_MINIMUM_FREE_BYTES = 6 * 1024**3

    def __init__(
        self,
        environment_service: ComfyEnvironmentService,
        runner: CommandRunner | None = None,
        *,
        git: str | Path | None = None,
        launcher_executable: Path | None = None,
    ) -> None:
        self.environment_service = environment_service
        self.runner = runner or CommandRunner()
        discovered = str(git) if git is not None else shutil.which("git")
        self.git = Path(discovered).resolve() if discovered else Path("git")
        self._operation_lock = threading.Lock()
        self.launcher_executable = (
            launcher_executable.resolve() if launcher_executable else
            Path(sys.executable).resolve() if getattr(sys, "frozen", False) else None
        )

    def execute(
        self,
        request: InstallationRequest,
        runtime: RuntimeProbe,
        *,
        torch_expectation: TorchExpectation = TorchExpectation(),
        torch_packages: tuple[str, ...] = ("torch", "torchvision", "torchaudio"),
        torch_index_url: str | None = None,
        on_chunk: ChunkCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> InstallationResult:
        target = Path(os.path.abspath(request.target))
        if not self._operation_lock.acquire(blocking=False):
            return InstallationResult(
                False,
                InstallationStage.PREFLIGHT,
                "Another installation operation is already running.",
                target,
            )
        try:
            return self._execute(
                request,
                target,
                runtime,
                torch_expectation,
                torch_packages,
                torch_index_url,
                on_chunk,
                cancel_event,
            )
        finally:
            self._operation_lock.release()

    def _execute(
        self,
        request: InstallationRequest,
        target: Path,
        runtime: RuntimeProbe,
        expectation: TorchExpectation,
        torch_packages: tuple[str, ...],
        torch_index_url: str | None,
        on_chunk: ChunkCallback | None,
        cancel_event: threading.Event | None,
    ) -> InstallationResult:
        required_free = (
            self.CUDA_MINIMUM_FREE_BYTES
            if expectation.require_cuda
            else self.CPU_MINIMUM_FREE_BYTES
        )
        error = self._preflight_error(target, request.repository_url, required_free,
                                      self.launcher_executable)
        if error:
            return InstallationResult(False, InstallationStage.PREFLIGHT, error, target)
        alongside = bool(target.is_dir() and any(target.iterdir()))
        target_was_empty = target.is_dir() and not alongside
        placed: list[str] = []
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return InstallationResult(
                False,
                InstallationStage.PREFLIGHT,
                f"Cannot create the installation parent folder: {exc}",
                target,
            )
        operation_id = uuid.uuid4().hex
        staging = target.with_name(f".{target.name}.installing-{operation_id[:10]}")

        clone = self.runner.run(
            CommandSpec(
                argv=(
                    str(self.git),
                    "clone",
                    "--origin",
                    "origin",
                    "--progress",
                    request.repository_url,
                    str(staging),
                ),
                cwd=target.parent,
                env={"GIT_TERMINAL_PROMPT": "0"},
                source="git-clone",
                timeout=1800.0,
            ),
            on_chunk=on_chunk,
            cancel_event=cancel_event,
        )
        if not clone.succeeded:
            cleaned, detail = self._cleanup_staging(staging)
            return InstallationResult(
                False,
                InstallationStage.CLONE if cleaned else InstallationStage.ROLLBACK,
                self._command_detail(clone) + detail,
                target,
                rolled_back=cleaned,
                recovery_path=None if cleaned else staging,
            )

        if cancel_event is not None and cancel_event.is_set():
            cleaned, detail = self._cleanup_staging(staging)
            return InstallationResult(
                False,
                InstallationStage.ROLLBACK,
                "Installation was cancelled." + detail,
                target,
                rolled_back=cleaned,
                recovery_path=None if cleaned else staging,
            )

        try:
            repository = ComfyRepository(staging, self.runner, git=self.git)
            plan = repository.prepare(request.channel, fetch=False)
            switched = repository.apply(plan)
        except RepositoryError as exc:
            return self._fail_staging(target, staging, InstallationStage.SELECT_REVISION, str(exc))
        if not switched.succeeded:
            return self._fail_staging(
                target, staging, InstallationStage.SELECT_REVISION, switched.detail
            )
        if not (staging / "main.py").is_file() or not (staging / "requirements.txt").is_file():
            return self._fail_staging(
                target,
                staging,
                InstallationStage.SELECT_REVISION,
                "The selected repository is missing ComfyUI main.py or requirements.txt.",
            )

        marker = staging / self.MARKER
        try:
            marker.write_text(
                json.dumps({"operation_id": operation_id, "target": str(target)}),
                encoding="utf-8",
            )
        except OSError as exc:
            return self._fail_staging(
                target,
                staging,
                InstallationStage.PLACE_CORE,
                f"Could not create the installation ownership marker: {exc}",
                recreate_empty_target=target_was_empty,
            )
        try:
            if alongside:
                error = self._preflight_error(target, request.repository_url, 0,
                                              self.launcher_executable)
                if error:
                    raise OSError(error)
                entries = sorted(staging.iterdir(), key=lambda path: path.name != self.MARKER)
                if any(os.path.lexists(target / entry.name) for entry in entries):
                    raise OSError("Prepared checkout conflicts with an existing file.")
                # The running EXE cannot be moved on Windows; only transfer owned entries.
                for entry in entries:
                    if os.path.lexists(target / entry.name):
                        raise OSError(f"Destination appeared during installation: {entry.name}")
                    entry.rename(target / entry.name)
                    placed.append(entry.name)
                staging.rmdir()
                exclude = target / ".git" / "info" / "exclude"
                name = self.launcher_executable.name
                pattern = "".join("\\" + c if c in "\\[]*?!# " else c for c in name)
                with exclude.open("a", encoding="utf-8") as handle:
                    handle.write("\n/" + pattern + "\n")
            else:
                if target_was_empty:
                    target.rmdir()
                staging.rename(target)
        except OSError as exc:
            if alongside and placed:
                cleaned, detail = self._cleanup_target(target, operation_id, placed)
                if not cleaned:
                    return InstallationResult(False, InstallationStage.ROLLBACK,
                                              str(exc) + detail, target, recovery_path=target)
            return self._fail_staging(
                target,
                staging,
                InstallationStage.PLACE_CORE,
                f"Could not place the prepared checkout: {exc}",
                recreate_empty_target=target_was_empty,
            )

        manifests = tuple(
            target / name
            for name in ComfyRepository.MANIFESTS
            if (target / name).is_file()
        )
        repaired = self.environment_service.rebuild(
            target / "venv",
            runtime,
            manifests,
            expectation=expectation,
            torch_packages=torch_packages,
            torch_index_url=torch_index_url,
            on_chunk=on_chunk,
            cancel_event=cancel_event,
        )
        if not repaired.succeeded:
            cleaned, detail = self._cleanup_target(
                target, operation_id, placed + ["venv"] if alongside else None,
            )
            if target_was_empty and cleaned:
                target.mkdir(parents=True, exist_ok=True)
            return InstallationResult(
                False,
                InstallationStage.ROLLBACK,
                repaired.detail + detail,
                target,
                switched.final_commit,
                rolled_back=cleaned,
                recovery_path=None if cleaned else target,
            )

        try:
            (target / self.MARKER).unlink()
        except OSError as exc:
            return InstallationResult(
                True,
                InstallationStage.COMMIT,
                f"Installation succeeded, but its temporary marker remains: {exc}",
                target,
                switched.final_commit,
                recovery_path=target / self.MARKER,
            )
        return InstallationResult(
            True,
            InstallationStage.COMMIT,
            "ComfyUI and its isolated environment were installed and validated.",
            target,
            switched.final_commit,
        )

    @staticmethod
    def _preflight_error(
        target: Path,
        repository_url: str,
        required_free_bytes: int = 0,
        launcher_executable: Path | None = None,
    ) -> str | None:
        if target == Path(target.anchor) or not target.name:
            return f"Unsafe installation path: {target}"
        if not repository_url.strip():
            return "Repository URL cannot be empty."
        try:
            is_junction = getattr(target, "is_junction", lambda: False)
            if target.is_symlink() or is_junction():
                return "The installation folder cannot be a link or junction."
            if target.exists():
                if not target.is_dir():
                    return "The installation path must be a folder."
                unexpected = [path for path in target.iterdir()
                              if launcher_executable is None
                              or path.is_symlink() or not path.is_file()
                              or path.resolve() != launcher_executable]
                if unexpected:
                    return ("Папка установки должна быть пустой или содержать только запущенный "
                            "EXE лаунчера. Найдено: " + ", ".join(p.name for p in unexpected[:5]))
            disk_root = target
            while not disk_root.exists() and disk_root != disk_root.parent:
                disk_root = disk_root.parent
            if required_free_bytes and disk_root.exists():
                free = shutil.disk_usage(disk_root).free
                if free < required_free_bytes:
                    required_gib = required_free_bytes / 1024**3
                    free_gib = free / 1024**3
                    return (
                        f"Not enough free space for this profile: {free_gib:.1f} GiB "
                        f"available, at least {required_gib:.0f} GiB required."
                    )
        except OSError as exc:
            return f"Cannot inspect the installation folder: {exc}"
        return None

    def _fail_staging(
        self,
        target: Path,
        staging: Path,
        stage: InstallationStage,
        detail: str,
        *,
        recreate_empty_target: bool = False,
    ) -> InstallationResult:
        cleaned, cleanup_detail = self._cleanup_staging(staging)
        if recreate_empty_target and cleaned:
            target.mkdir(parents=True, exist_ok=True)
        return InstallationResult(
            False,
            stage if cleaned else InstallationStage.ROLLBACK,
            detail + cleanup_detail,
            target,
            rolled_back=cleaned,
            recovery_path=None if cleaned else staging,
        )

    @staticmethod
    def _cleanup_staging(staging: Path) -> tuple[bool, str]:
        try:
            if staging.exists():
                _remove_tree(staging)
            return True, ""
        except OSError as exc:
            return False, f" Cleanup failed: {exc}"

    def _cleanup_target(self, target: Path, operation_id: str,
                        owned_names: list[str] | None = None) -> tuple[bool, str]:
        marker = target / self.MARKER
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
            if payload.get("operation_id") != operation_id:
                return False, " Refusing cleanup because the ownership marker changed."
            if owned_names is None:
                _remove_tree(target)
            else:
                for name in owned_names:
                    if name == self.MARKER:
                        continue
                    path = target / name
                    if path.parent != target or path == self.launcher_executable:
                        raise OSError("Refusing to remove an unowned path.")
                    if path.is_symlink() or path.is_junction():
                        raise OSError("Refusing to follow a link during rollback.")
                    if path.is_dir():
                        _remove_tree(path)
                    elif path.exists():
                        path.unlink()
                marker.unlink()
            return True, ""
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return False, f" Cleanup failed: {exc}"

    @staticmethod
    def _command_detail(result) -> str:
        return result.exception or result.stderr.strip() or result.stdout.strip() or result.failure.value


def _remove_tree(path: Path) -> None:
    def clear_readonly(function, name, _error) -> None:
        os.chmod(name, stat.S_IWRITE)
        function(name)

    shutil.rmtree(path, onexc=clear_readonly)
