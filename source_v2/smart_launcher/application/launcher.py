from __future__ import annotations

from smart_launcher.application.server_controller import ServerController, ServerLaunch
from smart_launcher.application.custom_node_controller import CustomNodeController
from smart_launcher.domain.custom_nodes import CustomNodeRequest
from smart_launcher.application.installation_controller import (
    InstallationController,
    InstallationJob,
)
from smart_launcher.application.update_pipeline import CoreUpdatePipeline
from smart_launcher.application.maintenance_controller import (
    MaintenanceController,
    MaintenanceRequest,
)
from smart_launcher.application.operation_coordinator import OperationCoordinator
from smart_launcher.domain.comfy import ServerState
from smart_launcher.domain.dependencies import TorchExpectation
from smart_launcher.domain.maintenance import MaintenanceMode
from smart_launcher.domain.events import DomainEvent, EventSeverity
from smart_launcher.domain.settings import LauncherSettings


class LauncherApplication:
    """Small application facade used by the UI composition root."""

    def __init__(
        self,
        settings: LauncherSettings | None,
        controller: ServerController,
        maintenance: MaintenanceController | None = None,
        installation: InstallationController | None = None,
        coordinator: OperationCoordinator | None = None,
        custom_nodes: CustomNodeController | None = None,
    ) -> None:
        self.settings = settings
        self.controller = controller
        self.maintenance = maintenance
        self.installation = installation
        self.coordinator = coordinator or OperationCoordinator()
        self.custom_nodes = custom_nodes

    @property
    def state(self) -> ServerState:
        return self.controller.state

    def start(self) -> None:
        try:
            if self.operation_running:
                raise RuntimeError(
                    "Нельзя запустить ComfyUI во время обслуживания или установки."
                )
            settings = self.settings
            if settings is None:
                raise RuntimeError("Smart Launcher ещё не настроен.")
            if self.installation_required:
                raise RuntimeError("В выбранной папке не установлен ComfyUI.")
            if not settings.python_executable.is_file():
                raise RuntimeError(
                    f"Не найден Python изолированного окружения: {settings.python_executable}"
                )
            if not (settings.venv_path / "pyvenv.cfg").is_file():
                raise RuntimeError(
                    f"Выбранное окружение не является venv: {settings.venv_path}"
                )
            self.controller.start(
                ServerLaunch(
                    python=settings.python_executable,
                    root=settings.comfy_root,
                    host=settings.listen_host,
                    port=settings.port,
                    arguments=settings.launch_arguments,
                )
            )
        except Exception as exc:
            self.controller.journal.append(
                DomainEvent(
                    "ServerStartRejected",
                    "Не удалось запустить ComfyUI",
                    EventSeverity.ERROR,
                    str(exc),
                )
            )
            raise

    def stop(self) -> bool:
        return self.controller.stop()

    @property
    def maintenance_running(self) -> bool:
        return self.maintenance is not None and self.maintenance.running

    @property
    def installation_running(self) -> bool:
        return self.installation is not None and self.installation.running

    @property
    def operation_running(self) -> bool:
        return (
            self.coordinator.busy
            or self.maintenance_running
            or self.installation_running
            or (self.custom_nodes is not None and self.custom_nodes.running)
        )

    def start_custom_node(self, request: CustomNodeRequest) -> bool:
        if (
            self.custom_nodes is None
            or self.state not in (ServerState.STOPPED, ServerState.FAILED)
        ):
            return False
        return self.custom_nodes.start(request)

    @property
    def installation_required(self) -> bool:
        return self.settings is None or not (self.settings.comfy_root / "main.py").is_file()

    @property
    def configured(self) -> bool:
        return self.settings is not None

    def start_maintenance(self) -> bool:
        return self._start_maintenance(MaintenanceMode.CORE_UPDATE)

    def check_updates(self) -> bool:
        return self._start_maintenance(MaintenanceMode.UPDATE_CHECK)

    def start_environment_repair(self) -> bool:
        return self._start_maintenance(MaintenanceMode.ENVIRONMENT_REPAIR)

    def _start_maintenance(self, mode: MaintenanceMode) -> bool:
        settings = self.settings
        if settings is None or self.maintenance is None or self.installation_running or self.state not in (
            ServerState.STOPPED,
            ServerState.FAILED,
        ):
            return False
        return self.maintenance.start(
            MaintenanceRequest(
                channel=settings.update_channel,
                environment=settings.venv_path,
                base_python=settings.base_python,
                torch_packages=settings.torch_packages,
                torch_index_url=settings.torch_index_url,
                torch_expectation=TorchExpectation(
                    require_cuda=settings.torch_require_cuda,
                    cuda_major=settings.torch_cuda_major,
                ),
                mode=mode,
                approved_plan=(
                    self.maintenance.available_plan
                    if mode is MaintenanceMode.CORE_UPDATE else None
                ),
            )
        )

    def cancel_maintenance(self) -> bool:
        return self.maintenance is not None and self.maintenance.cancel()

    def start_installation(self, job: InstallationJob) -> bool:
        if (
            self.installation is None
            or self.maintenance_running
            or self.state not in (ServerState.STOPPED, ServerState.FAILED)
        ):
            return False
        return self.installation.start(job)

    def cancel_operation(self) -> bool:
        if self.custom_nodes is not None and self.custom_nodes.running:
            return self.custom_nodes.cancel()
        if self.installation_running and self.installation is not None:
            return self.installation.cancel()
        return self.cancel_maintenance()

    def reconfigure(
        self,
        settings: LauncherSettings,
        maintenance: MaintenanceController,
    ) -> None:
        if self.maintenance_running:
            raise RuntimeError("Нельзя менять настройки во время обслуживания.")
        self.settings = settings
        previous = self.maintenance
        self.maintenance = maintenance
        if previous is not None and previous is not maintenance:
            previous.close()

    def close(self) -> None:
        if self.custom_nodes is not None:
            self.custom_nodes.close()
        if self.installation is not None:
            self.installation.close()
        if self.maintenance is not None:
            self.maintenance.close()
        if self.state is not ServerState.STOPPED:
            self.stop()
