from __future__ import annotations

import os
import shutil
import stat
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from smart_launcher.domain.commands import CommandFailure, CommandSpec, StreamChunk
from smart_launcher.domain.custom_nodes import (
    CustomNodeRequest,
    CustomNodeResult,
    parse_github_origin,
    parse_github_repository,
)
from smart_launcher.infrastructure.command_runner import CommandRunner


class CustomNodePipeline:
    def __init__(
        self,
        runner: CommandRunner,
        *,
        server_is_stopped: Callable[[], bool],
    ) -> None:
        self.runner = runner
        self.server_is_stopped = server_is_stopped

    def execute(
        self,
        request: CustomNodeRequest,
        *,
        on_chunk: Callable[[StreamChunk], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> CustomNodeResult:
        cancel = cancel_event or threading.Event()
        if request.repair_dependencies:
            return self._repair_dependencies(request, on_chunk, cancel)
        if request.remove_target is not None:
            return self._remove_node(request, on_chunk, cancel)
        if request.registry_id:
            return self._update_registry_node(request, on_chunk, cancel)
        try:
            repository = parse_github_repository(request.repository_url)
        except ValueError as exc:
            return CustomNodeResult(False, str(exc))
        if not self.server_is_stopped():
            return CustomNodeResult(False, "Остановите ComfyUI перед установкой Custom Nodes.")

        root = request.comfy_root.resolve()
        if not (root / "main.py").is_file():
            return CustomNodeResult(False, f"Папка не похожа на ComfyUI: {root}")
        python = (request.venv_path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")).resolve()
        if request.install_requirements and not python.is_file():
            return CustomNodeResult(False, f"Python окружения не найден: {python}")

        raw_custom_nodes = root / "custom_nodes"
        if not os.path.lexists(raw_custom_nodes):
            raw_custom_nodes.mkdir(parents=True)
        if not raw_custom_nodes.is_dir():
            return CustomNodeResult(
                False,
                "Путь custom_nodes должен быть папкой или ссылкой на существующую папку.",
            )
        try:
            custom_nodes = raw_custom_nodes.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            return CustomNodeResult(False, f"Не удалось открыть папку custom_nodes: {exc}")

        raw_target = custom_nodes / repository.name
        if os.path.lexists(raw_target) and _is_link_or_junction(raw_target):
            return CustomNodeResult(
                False,
                "Папка отдельной Custom Node не должна быть ссылкой или junction.",
                raw_target,
            )
        target = raw_target.resolve()
        if target.parent != custom_nodes:
            return CustomNodeResult(False, "Небезопасный путь назначения Custom Node.")

        operation = uuid.uuid4().hex
        staging = custom_nodes / f".smart-launcher-stage-{repository.name}-{operation}"
        backup = custom_nodes / f".smart-launcher-backup-{repository.name}-{operation}"
        existed = target.exists()
        try:
            self._cleanup_stale_staging(custom_nodes, repository.name)
            if existed:
                validation = self._validate_existing(
                    target, repository.clone_url, on_chunk, cancel
                )
                if validation is not None:
                    return CustomNodeResult(False, validation, target)

            clone = self._run(
                (
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "--recurse-submodules",
                    "--shallow-submodules",
                    "--progress",
                    repository.clone_url,
                    str(staging),
                ),
                custom_nodes,
                "custom-node-git",
                on_chunk,
                cancel,
                timeout=900,
                idle_timeout=60,
                env={"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"},
            )
            failure = self._failure_detail(clone, "Не удалось клонировать репозиторий")
            if failure:
                return CustomNodeResult(
                    False, failure, target if existed else None, cancelled=clone.failure is CommandFailure.CANCELLED
                )

            requirements = staging / "requirements.txt"
            if request.install_requirements and requirements.is_file():
                install = self._run(
                    (str(python), "-m", "pip", "install", "-r", str(requirements)),
                    root,
                    "custom-node-pip",
                    on_chunk,
                    cancel,
                    timeout=3600,
                )
                failure = self._failure_detail(install, "Не удалось установить зависимости ноды")
                if failure:
                    return CustomNodeResult(
                        False, failure, target if existed else None, cancelled=install.failure is CommandFailure.CANCELLED
                    )

            if cancel.is_set():
                return CustomNodeResult(False, "Операция отменена.", target if existed else None, cancelled=True)
            if existed:
                os.replace(target, backup)
            try:
                os.replace(staging, target)
            except Exception:
                if existed and backup.exists() and not target.exists():
                    os.replace(backup, target)
                raise
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
            action = "обновлена" if existed else "установлена"
            return CustomNodeResult(True, f"Нода {repository.name} {action}.", target, existed)
        except Exception as exc:
            return CustomNodeResult(False, f"{type(exc).__name__}: {exc}", target if existed else None)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if backup.exists() and not target.exists():
                try:
                    os.replace(backup, target)
                except OSError:
                    pass

    def _update_registry_node(
        self,
        request: CustomNodeRequest,
        on_chunk: Callable[[StreamChunk], None] | None,
        cancel: threading.Event,
    ) -> CustomNodeResult:
        if not self.server_is_stopped():
            return CustomNodeResult(False, "Остановите ComfyUI перед обновлением Custom Nodes.")
        root = request.comfy_root.resolve()
        if not (root / "main.py").is_file():
            return CustomNodeResult(False, f"Папка не похожа на ComfyUI: {root}")
        executable = request.venv_path / (
            "Scripts/cm-cli.exe" if os.name == "nt" else "bin/cm-cli"
        )
        if not executable.is_file():
            return CustomNodeResult(False, "ComfyUI Manager CLI не найден в окружении ComfyUI.")
        registry_id = request.registry_id.strip()
        if not registry_id or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-_." for char in registry_id.casefold()):
            return CustomNodeResult(False, "Некорректный идентификатор Registry.")
        result = self._run(
            (str(executable), "update", registry_id),
            root,
            "custom-node-manager",
            on_chunk,
            cancel,
            timeout=1800,
            idle_timeout=120,
            env={"COMFYUI_PATH": str(root)},
        )
        failure = self._failure_detail(result, "Manager не смог обновить ноду")
        if failure:
            return CustomNodeResult(
                False,
                failure,
                cancelled=result.failure is CommandFailure.CANCELLED,
            )
        return CustomNodeResult(True, f"Registry-нода {registry_id} обновлена.", updated=True)

    def _remove_node(
        self,
        request: CustomNodeRequest,
        on_chunk: Callable[[StreamChunk], None] | None,
        cancel: threading.Event,
    ) -> CustomNodeResult:
        if not self.server_is_stopped():
            return CustomNodeResult(False, "Остановите ComfyUI перед удалением Custom Nodes.")
        root = request.comfy_root.resolve()
        if not (root / "main.py").is_file():
            return CustomNodeResult(False, f"Папка не похожа на ComfyUI: {root}")
        custom_nodes = (root / "custom_nodes").resolve(strict=True)
        target = request.remove_target
        if target is None or _is_link_or_junction(target):
            return CustomNodeResult(False, "Небезопасный путь Custom Node.")
        target = target.resolve(strict=True)
        removable = target.is_dir() or (
            target.is_file() and target.suffix.casefold() == ".py"
        )
        if target.parent != custom_nodes or not removable or target.name.startswith("."):
            return CustomNodeResult(False, "Небезопасный путь Custom Node.")

        if request.registry_id:
            executable = request.venv_path / (
                "Scripts/cm-cli.exe" if os.name == "nt" else "bin/cm-cli"
            )
            if not executable.is_file():
                return CustomNodeResult(False, "ComfyUI Manager CLI не найден в окружении ComfyUI.")
            result = self._run(
                (str(executable), "uninstall", request.registry_id),
                root,
                "custom-node-manager",
                on_chunk,
                cancel,
                timeout=900,
                idle_timeout=120,
                env={"COMFYUI_PATH": str(root)},
            )
            failure = self._failure_detail(result, "Manager не смог удалить ноду")
            if failure:
                return CustomNodeResult(
                    False, failure, target, cancelled=result.failure is CommandFailure.CANCELLED
                )
            return CustomNodeResult(True, f"Нода {target.name} удалена.", target)

        trash = custom_nodes / f".smart-launcher-remove-{target.name}-{uuid.uuid4().hex}"
        try:
            os.replace(target, trash)
            if trash.is_dir():
                _remove_tree(trash)
            else:
                _retry_remove(trash.unlink, trash)
        except Exception as exc:
            if trash.exists() and not target.exists():
                try:
                    os.replace(trash, target)
                except OSError:
                    pass
            return CustomNodeResult(False, f"Не удалось удалить {target.name}: {exc}", target)
        return CustomNodeResult(True, f"Нода {target.name} удалена.", target)

    def _repair_dependencies(
        self,
        request: CustomNodeRequest,
        on_chunk: Callable[[StreamChunk], None] | None,
        cancel: threading.Event,
    ) -> CustomNodeResult:
        if not self.server_is_stopped():
            return CustomNodeResult(
                False, "Остановите ComfyUI перед восстановлением зависимостей."
            )
        root = request.comfy_root.resolve()
        if not (root / "main.py").is_file():
            return CustomNodeResult(False, f"Папка не похожа на ComfyUI: {root}")
        executable = request.venv_path / (
            "Scripts/cm-cli.exe" if os.name == "nt" else "bin/cm-cli"
        )
        if not executable.is_file():
            return CustomNodeResult(False, "ComfyUI Manager CLI не найден в окружении ComfyUI.")
        result = self._run(
            (str(executable), "fix", "all"),
            root,
            "custom-node-dependencies",
            on_chunk,
            cancel,
            timeout=3600,
            # Some packages spend several minutes compiling or resolving without
            # producing output. The overall timeout and explicit cancellation are
            # sufficient for this deliberately long-running maintenance command.
            idle_timeout=None,
            env={"COMFYUI_PATH": str(root)},
        )
        failure = self._failure_detail(
            result, "Manager не смог восстановить зависимости нод"
        )
        if failure:
            return CustomNodeResult(
                False,
                failure,
                cancelled=result.failure is CommandFailure.CANCELLED,
            )
        return CustomNodeResult(
            True,
            "Зависимости Custom Nodes восстановлены. Перезапустите ComfyUI.",
        )

    def _validate_existing(
        self,
        target: Path,
        expected_url: str,
        on_chunk: Callable[[StreamChunk], None] | None,
        cancel: threading.Event,
    ) -> str | None:
        if not target.is_dir() or not (target / ".git").is_dir():
            return f"Папка уже существует и не является Git-репозиторием: {target}"
        origin = self._run(
            ("git", "remote", "get-url", "origin"), target, "custom-node-check", on_chunk, cancel
        )
        if not origin.succeeded:
            return self._failure_detail(origin, "Не удалось проверить origin существующей ноды")
        try:
            actual = parse_github_origin(origin.stdout.strip()).clone_url
        except ValueError:
            return "Origin существующей ноды не является поддерживаемой GitHub-ссылкой."
        if actual.casefold() != expected_url.casefold():
            return f"Папка с таким именем принадлежит другому репозиторию: {actual}"
        status = self._run(
            ("git", "status", "--porcelain"), target, "custom-node-check", on_chunk, cancel
        )
        if not status.succeeded:
            return self._failure_detail(status, "Не удалось проверить состояние существующей ноды")
        if status.stdout.strip():
            return "В существующей ноде есть локальные изменения. Обновление отменено."
        return None

    def _run(
        self,
        argv,
        cwd,
        source,
        on_chunk,
        cancel,
        *,
        timeout=120,
        idle_timeout=None,
        env=None,
    ):
        return self.runner.run(
            CommandSpec(
                tuple(argv),
                Path(cwd),
                env=env or {},
                source=source,
                timeout=timeout,
                idle_timeout=idle_timeout,
            ),
            on_chunk=on_chunk,
            cancel_event=cancel,
        )

    @staticmethod
    def _cleanup_stale_staging(custom_nodes: Path, repository_name: str) -> None:
        for stale in custom_nodes.glob(f".smart-launcher-stage-{repository_name}-*"):
            if stale.is_dir() and not stale.is_symlink():
                shutil.rmtree(stale, ignore_errors=True)

    @staticmethod
    def _failure_detail(result, prefix: str) -> str | None:
        if result.succeeded:
            return None
        if result.failure is CommandFailure.CANCELLED:
            return "Операция отменена."
        if result.failure is CommandFailure.TIMEOUT:
            return f"{prefix}: операция слишком долго не отвечала."
        detail = result.stderr.strip() or result.stdout.strip() or result.exception or result.failure.value
        return f"{prefix}: {detail}"


def _retry_remove(function, path: Path, _error: BaseException | None = None) -> None:
    try:
        os.chmod(path, stat.S_IWRITE)
    except OSError:
        pass
    last_error: OSError | None = None
    for delay in (0.0, 0.05, 0.15, 0.35, 0.75):
        if delay:
            time.sleep(delay)
        try:
            function(path)
            return
        except OSError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error


def _remove_tree(path: Path) -> None:
    shutil.rmtree(path, onexc=_retry_remove)


def _is_link_or_junction(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()
