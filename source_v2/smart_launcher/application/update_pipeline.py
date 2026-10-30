from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from smart_launcher.comfy.repository import ComfyRepository, RepositoryError
from smart_launcher.application.environment_service import ComfyEnvironmentService
from smart_launcher.domain.dependencies import TorchExpectation
from smart_launcher.domain.environments import RepairResult
from smart_launcher.domain.maintenance import MaintenanceResult, MaintenanceStage
from smart_launcher.domain.runtimes import RuntimeProbe
from smart_launcher.domain.updates import UpdateChannel, UpdatePlan
from smart_launcher.infrastructure.command_runner import ChunkCallback
from smart_launcher.runtime.dependency_sync import DependencySynchronizer
from smart_launcher.runtime.core_smoke import ComfyCoreSmokeValidator
from smart_launcher.runtime.requirements import RequirementsValidator
from smart_launcher.runtime.torch_profile import TorchProfileValidator
from smart_launcher.runtime.venv_manager import VenvManager
from smart_launcher.runtime.package_integrity import PackageIntegrity
from smart_launcher.infrastructure.update_marker import UpdateMarker

StoppedProbe = Callable[[], bool]


class CoreUpdatePipeline:
    """Coordinates core and environment updates as one recoverable operation."""

    def __init__(
        self,
        repository: ComfyRepository,
        environments: VenvManager,
        dependencies: DependencySynchronizer,
        requirements: RequirementsValidator,
        torch: TorchProfileValidator,
        *,
        server_is_stopped: StoppedProbe,
        core_smoke: ComfyCoreSmokeValidator | None = None,
    ) -> None:
        self.repository = repository
        self.environments = environments
        self.dependencies = dependencies
        self.requirements = requirements
        self.torch = torch
        self.core_smoke = core_smoke or ComfyCoreSmokeValidator()
        self.environment_service = ComfyEnvironmentService(
            environments, dependencies, requirements, torch
        )
        self.server_is_stopped = server_is_stopped
        self._operation_lock = threading.Lock()
        self.update_marker = UpdateMarker(repository.root)
        self.integrity = PackageIntegrity()

    def execute(
        self,
        channel: UpdateChannel,
        environment: Path,
        runtime: RuntimeProbe,
        *,
        torch_expectation: TorchExpectation = TorchExpectation(),
        torch_packages: tuple[str, ...] = ("torch", "torchvision", "torchaudio"),
        torch_index_url: str | None = None,
        fetch: bool = True,
        on_chunk: ChunkCallback | None = None,
        cancel_event: threading.Event | None = None,
        approved_plan: UpdatePlan | None = None,
    ) -> MaintenanceResult:
        if not self._operation_lock.acquire(blocking=False):
            return MaintenanceResult(
                False,
                MaintenanceStage.PREFLIGHT,
                "Another maintenance operation is already running.",
            )
        try:
            return self._execute(
                channel,
                environment,
                runtime,
                torch_expectation=torch_expectation,
                torch_packages=torch_packages,
                torch_index_url=torch_index_url,
                fetch=fetch,
                on_chunk=on_chunk,
                cancel_event=cancel_event,
                approved_plan=approved_plan,
            )
        finally:
            self._operation_lock.release()

    def check_update(
        self,
        channel: UpdateChannel,
        *,
        fetch: bool = True,
        cancel_event: threading.Event | None = None,
    ) -> MaintenanceResult:
        if not self._operation_lock.acquire(blocking=False):
            return MaintenanceResult(
                False, MaintenanceStage.PREFLIGHT, "Another maintenance operation is already running."
            )
        try:
            if cancel_event is not None and cancel_event.is_set():
                return MaintenanceResult(False, MaintenanceStage.PREFLIGHT, "Update check was cancelled.")
            if self.update_marker.pending:
                return MaintenanceResult(
                    False, MaintenanceStage.PREFLIGHT,
                    "Предыдущее обновление ComfyUI не завершено или не прошло проверку. "
                    "Нажмите «Обновить» для повторной синхронизации и проверки файлов. "
                    "Переустановка всего окружения автоматически не выполняется.",
                )
            try:
                plan = self.repository.prepare(channel, fetch=fetch)
            except RepositoryError as exc:
                return MaintenanceResult(False, MaintenanceStage.PLAN, str(exc))
            detail = (
                plan.version_summary + "\n" + plan.dependency_summary
                if plan.update_available
                else "ComfyUI уже обновлён до актуальной версии."
            )
            return MaintenanceResult(True, MaintenanceStage.PLAN, detail, plan)
        finally:
            self._operation_lock.release()

    def _execute(
        self,
        channel: UpdateChannel,
        environment: Path,
        runtime: RuntimeProbe,
        *,
        torch_expectation: TorchExpectation,
        torch_packages: tuple[str, ...],
        torch_index_url: str | None,
        fetch: bool,
        on_chunk: ChunkCallback | None,
        cancel_event: threading.Event | None,
        approved_plan: UpdatePlan | None,
    ) -> MaintenanceResult:
        if not self.server_is_stopped():
            return MaintenanceResult(
                False,
                MaintenanceStage.PREFLIGHT,
                "ComfyUI must be stopped before an update.",
            )
        if cancel_event is not None and cancel_event.is_set():
            return MaintenanceResult(False, MaintenanceStage.PREFLIGHT, "Update was cancelled.")
        try:
            plan = self.repository.prepare(channel, fetch=fetch)
        except RepositoryError as exc:
            return MaintenanceResult(False, MaintenanceStage.PLAN, str(exc))
        if cancel_event is not None and cancel_event.is_set():
            return MaintenanceResult(
                False,
                MaintenanceStage.PLAN,
                "Update was cancelled before changing the installation.",
                plan,
            )

        if approved_plan is not None and (
            plan.root != approved_plan.root
            or plan.channel != approved_plan.channel
            or plan.current_commit != approved_plan.current_commit
            or plan.target_commit != approved_plan.target_commit
        ):
            return MaintenanceResult(
                False, MaintenanceStage.PLAN,
                "Версии изменились после предпросмотра. Повторите проверку обновлений.",
                plan,
            )

        recovering = self.update_marker.pending
        if not plan.update_available and not recovering:
            return MaintenanceResult(
                True,
                MaintenanceStage.COMMIT,
                "ComfyUI уже обновлён до актуальной версии.",
                plan,
            )

        health = self.environment_service.inspect(environment, runtime)
        if not health.healthy:
            return MaintenanceResult(
                False,
                MaintenanceStage.PREFLIGHT,
                "Окружение ComfyUI неисправно. Сначала запустите отдельную проверку окружения: "
                + health.detail,
                plan,
            )

        environment_sync_attempted = False
        self.update_marker.begin(plan)

        def validate(target: UpdatePlan) -> tuple[bool, str]:
            nonlocal environment_sync_attempted
            if cancel_event is not None and cancel_event.is_set():
                return False, "Update was cancelled."
            core_manifests = self._core_manifests(target.root)
            changed_manifests = core_manifests if recovering else self._changed_manifest_paths(target)
            if changed_manifests:
                environment_sync_attempted = True
                synchronized = self.dependencies.sync(
                    self.environments.python_path(environment),
                    changed_manifests,
                    upgrade=False,
                    on_chunk=on_chunk,
                    cancel_event=cancel_event,
                )
                if not synchronized.succeeded:
                    return False, synchronized.detail
            if recovering:
                checked = self.integrity.verify(
                    self.environments.python_path(environment),
                    cancel_event=cancel_event, on_chunk=on_chunk,
                )
                if not checked.succeeded:
                    return False, (
                        "Проверка целостности пакетов не пройдена. Требуется точечное восстановление: "
                        + (checked.exception or checked.stdout or checked.stderr)
                    )
            return self._validate_installation(
                self.environments.python_path(environment),
                target.root,
                core_manifests,
                torch_expectation,
                on_chunk,
                cancel_event,
            )

        if not plan.update_available:
            valid, detail = validate(plan)
            if not valid:
                return MaintenanceResult(False, MaintenanceStage.VALIDATE, detail, plan)
            self.update_marker.complete()
            return MaintenanceResult(True, MaintenanceStage.COMMIT,
                                     "Проверка незавершённого обновления успешно завершена.", plan)
        update = self.repository.apply(plan, validate=validate)
        if not update.succeeded:
            environment_rolled_back = False
            detail = update.detail
            if update.rolled_back and environment_sync_attempted:
                restored = self.dependencies.sync(
                    self.environments.python_path(environment),
                    self._changed_manifest_paths(plan),
                    upgrade=False,
                    on_chunk=on_chunk,
                    cancel_event=None,
                )
                environment_rolled_back = restored.succeeded
                if not restored.succeeded:
                    detail += " Не удалось вернуть прежние зависимости: " + restored.detail
            return MaintenanceResult(
                False,
                MaintenanceStage.ROLLBACK if update.rolled_back else MaintenanceStage.SWITCH_CORE,
                detail,
                plan,
                core_rolled_back=update.rolled_back,
                environment_rolled_back=environment_rolled_back,
            )
        self.update_marker.complete()
        return MaintenanceResult(
            True,
            MaintenanceStage.COMMIT,
            update.detail,
            plan,
        )

    def _repair_environment(
        self,
        environment: Path,
        runtime: RuntimeProbe,
        provision_manifests: tuple[Path, ...],
        core_manifests: tuple[Path, ...],
        core_root: Path,
        expectation: TorchExpectation,
        torch_packages: tuple[str, ...],
        torch_index_url: str | None,
        on_chunk: ChunkCallback | None,
        cancel_event: threading.Event | None,
        *,
        keep_backup: bool,
    ) -> RepairResult:
        return self.environment_service.rebuild(
            environment,
            runtime,
            provision_manifests,
            expectation=expectation,
            torch_packages=torch_packages,
            torch_index_url=torch_index_url,
            on_chunk=on_chunk,
            cancel_event=cancel_event,
            keep_backup=keep_backup,
            validate=lambda python: self._validate_installation(
                python,
                core_root,
                core_manifests,
                expectation,
                on_chunk,
                cancel_event,
            ),
        )

    def repair_environment(
        self,
        environment: Path,
        runtime: RuntimeProbe,
        *,
        torch_expectation: TorchExpectation = TorchExpectation(),
        torch_packages: tuple[str, ...] = ("torch", "torchvision", "torchaudio"),
        torch_index_url: str | None = None,
        on_chunk: ChunkCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> MaintenanceResult:
        if not self._operation_lock.acquire(blocking=False):
            return MaintenanceResult(
                False, MaintenanceStage.PREFLIGHT, "Another maintenance operation is already running."
            )
        try:
            if not self.server_is_stopped():
                return MaintenanceResult(
                    False, MaintenanceStage.PREFLIGHT, "ComfyUI must be stopped before repair."
                )
            if cancel_event is not None and cancel_event.is_set():
                return MaintenanceResult(False, MaintenanceStage.PREFLIGHT, "Repair was cancelled.")
            root = self.repository.root
            health = self.environment_service.inspect(environment, runtime)
            core_manifests = self._core_manifests(root)
            if health.healthy:
                valid, detail = self.environment_service.validate(
                    self.environments.python_path(environment),
                    core_manifests,
                    torch_expectation,
                )
                if valid:
                    return MaintenanceResult(True, MaintenanceStage.COMMIT, detail)
            repaired = self._repair_environment(
                environment,
                runtime,
                self._provision_manifests(root),
                core_manifests,
                root,
                torch_expectation,
                torch_packages,
                torch_index_url,
                on_chunk,
                cancel_event,
                keep_backup=False,
            )
            return MaintenanceResult(
                repaired.succeeded,
                MaintenanceStage.COMMIT if repaired.succeeded else MaintenanceStage.ROLLBACK,
                repaired.detail,
                environment_rolled_back=repaired.rolled_back,
                recovery_backup=repaired.backup,
            )
        finally:
            self._operation_lock.release()

    def _core_manifests(self, root: Path) -> tuple[Path, ...]:
        return tuple(
            root / name
            for name in self.repository.MANIFESTS
            if (root / name).is_file()
        )

    @staticmethod
    def _changed_manifest_paths(plan: UpdatePlan) -> tuple[Path, ...]:
        return tuple(
            plan.root / name
            for name in plan.changed_manifests
            if (plan.root / name).is_file()
        )

    def _provision_manifests(self, root: Path) -> tuple[Path, ...]:
        manifests = list(self._core_manifests(root))
        custom_nodes = root / "custom_nodes"
        if custom_nodes.is_dir():
            manifests.extend(
                path
                for path in sorted(custom_nodes.glob("*/requirements.txt"))
                if path.is_file()
            )
        return tuple(dict.fromkeys(manifests))

    def _validate_installation(
        self,
        python: Path,
        root: Path,
        manifests: tuple[Path, ...],
        expectation: TorchExpectation,
        on_chunk: ChunkCallback | None,
        cancel_event: threading.Event | None,
    ) -> tuple[bool, str]:
        valid, detail = self.environment_service.validate(python, manifests, expectation)
        if not valid:
            return False, detail
        return self.core_smoke.validate(
            python,
            root,
            on_chunk=on_chunk,
            cancel_event=cancel_event,
        )
