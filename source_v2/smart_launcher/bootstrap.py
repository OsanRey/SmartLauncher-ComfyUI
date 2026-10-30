from __future__ import annotations

from dataclasses import replace
import os
import sys
from pathlib import Path
import webbrowser

import customtkinter as ctk

from smart_launcher.application.environment_service import ComfyEnvironmentService
from smart_launcher.application.download_controller import DownloadController
from smart_launcher.application.custom_node_controller import CustomNodeController
from smart_launcher.application.custom_node_catalog import CustomNodeCatalog
from smart_launcher.application.custom_node_pipeline import CustomNodePipeline
from smart_launcher.application.existing_installation import (
    ExistingInstallationProfile,
    ExistingInstallationService,
)
from smart_launcher.application.installation_controller import InstallationController
from smart_launcher.application.installation_pipeline import InstallationPipeline
from smart_launcher.application.launcher import LauncherApplication
from smart_launcher.application.maintenance_controller import MaintenanceController
from smart_launcher.application.operation_coordinator import OperationCoordinator
from smart_launcher.application.server_controller import ServerController
from smart_launcher.application.update_pipeline import CoreUpdatePipeline
from smart_launcher.comfy.repository import ComfyRepository
from smart_launcher.domain.comfy import ServerState
from smart_launcher.domain.events import DomainEvent, EventSeverity
from smart_launcher.domain.settings import LauncherSettings
from smart_launcher.domain.updates import UpdateChannel
from smart_launcher.infrastructure.config_store import ConfigError, ConfigStore, default_config_path
from smart_launcher.infrastructure.diagnostic_log import DiagnosticLog
from smart_launcher.runtime.dependency_sync import DependencySynchronizer
from smart_launcher.runtime.python_resolver import PythonResolver
from smart_launcher.runtime.requirements import RequirementsValidator
from smart_launcher.runtime.torch_profile import TorchProfileValidator
from smart_launcher.runtime.venv_manager import VenvManager
from smart_launcher.infrastructure.command_runner import CommandRunner
from smart_launcher.infrastructure.network import lan_url
from smart_launcher.infrastructure.stores import EventJournal, LogStore
from smart_launcher.downloads.resolver import ModelLinkResolver
from smart_launcher.downloads.transport import ModelDownloadTransport
from smart_launcher.ui.downloader_window import DownloaderWindow
from smart_launcher.ui.components_dialog import ComponentsDialog
from smart_launcher.ui.main_window import MainWindow
from smart_launcher.ui.install_wizard import InstallWizard
from smart_launcher.ui.setup_dialog import SetupDialog
from smart_launcher.ui.settings_dialog import SettingsDialog
from smart_launcher.ui.update_dialog import UpdateDialog


def run(config_path: Path | None = None) -> int:
    store = ConfigStore(config_path or default_config_path())
    diagnostics = DiagnosticLog(store.path.parent / "logs")
    logs = LogStore(on_append=diagnostics.append_log)
    journal = EventJournal(on_append=diagnostics.append_event)
    controller = ServerController(logs=logs, journal=journal)
    try:
        settings = store.load_optional()
    except ConfigError as exc:
        settings = None
        controller.journal.append(
            DomainEvent(
                "ConfigurationRejected",
                "Конфигурация 2.0 не загружена",
                EventSeverity.ERROR,
                str(exc),
            )
        )
    if settings is not None:
        diagnostics.set_secrets(
            (settings.civitai_token, settings.huggingface_token)
        )
    operations = OperationCoordinator()
    maintenance = (
        _make_maintenance(settings.comfy_root, controller, operations)
        if settings
        else None
    )
    application = LauncherApplication(
        settings, controller, maintenance, coordinator=operations
    )
    custom_nodes = CustomNodeController(
        CustomNodePipeline(
            CommandRunner(),
            server_is_stopped=lambda: controller.state
            in (ServerState.STOPPED, ServerState.FAILED),
        ),
        controller.logs,
        controller.journal,
        controller.progress,
        operations,
    )
    application.custom_nodes = custom_nodes
    environment = _make_environment_service()
    installer = InstallationController(
        InstallationPipeline(environment),
        PythonResolver(),
        controller.logs,
        controller.journal,
        controller.progress,
        coordinator=operations,
    )
    application.installation = installer
    downloads = DownloadController(
        ModelDownloadTransport(),
        controller.logs,
        controller.journal,
        controller.progress,
    )
    existing = ExistingInstallationService()
    ctk.set_appearance_mode("dark")
    wizard: InstallWizard | None = None
    setup: SetupDialog | None = None
    settings_window: SettingsDialog | None = None
    downloader_window: DownloaderWindow | None = None
    components_window: ComponentsDialog | None = None
    closing = False

    def open_install_wizard() -> None:
        nonlocal wizard
        if wizard is not None and wizard.winfo_exists():
            wizard.lift()
            wizard.focus_force()
            return
        wizard = InstallWizard(
            window,
            initial_target=_default_install_target(),
            initial_python=application.settings.base_python if application.settings else None,
            initial_channel=(
                application.settings.update_channel
                if application.settings
                else UpdateChannel.STABLE
            ),
            on_install=application.start_installation,
        )

    def connect_existing(root, expectation) -> ExistingInstallationProfile:
        return existing.connect(root, expectation=expectation)

    def connected(profile: ExistingInstallationProfile) -> None:
        apply_settings(profile.settings)
        controller.journal.append(
            DomainEvent(
                "ExistingInstallationConnected",
                "Существующий ComfyUI подключён",
                EventSeverity.SUCCESS,
                profile.detail,
            )
        )

    def open_setup() -> None:
        nonlocal setup
        if setup is not None and setup.winfo_exists():
            setup.lift()
            setup.focus_force()
            return
        setup = SetupDialog(
            window,
            on_new_install=open_install_wizard,
            on_connect=connect_existing,
            on_connected=connected,
        )

    def open_settings() -> None:
        nonlocal settings_window
        if application.settings is None:
            open_setup()
            return
        if settings_window is not None and settings_window.winfo_exists():
            settings_window.lift()
            settings_window.focus_force()
            return
        settings_window = SettingsDialog(
            window,
            application.settings,
            on_save=apply_settings,
            on_repair_environment=application.start_environment_repair,
            on_open_logs=lambda: _open_directory(diagnostics.directory),
        )

    def tokens_changed(civitai: str | None, huggingface: str | None) -> None:
        current = application.settings
        if current is None or (
            current.civitai_token == civitai
            and current.huggingface_token == huggingface
        ):
            return
        apply_settings(
            replace(
                current,
                civitai_token=civitai,
                huggingface_token=huggingface,
            )
        )

    def open_downloader() -> None:
        nonlocal downloader_window
        if application.settings is None:
            open_setup()
            return
        if downloader_window is not None and downloader_window.winfo_exists():
            downloader_window.lift()
            downloader_window.focus_force()
            return
        downloader_window = DownloaderWindow(
            window,
            application.settings,
            downloads,
            ModelLinkResolver(),
            controller.progress,
            custom_nodes,
            CustomNodeCatalog(CommandRunner()),
            on_tokens_changed=tokens_changed,
            on_custom_node_start=application.start_custom_node,
            on_custom_node_cancel=custom_nodes.cancel,
        )

    def open_components() -> None:
        nonlocal components_window
        if application.settings is None:
            open_setup()
            return
        if components_window is not None and components_window.winfo_exists():
            components_window.lift()
            components_window.focus_force()
            return
        components_window = ComponentsDialog(window, application.settings)

    def open_comfy_browser() -> None:
        current = application.settings
        if current is not None:
            webbrowser.open(f"http://127.0.0.1:{current.port}", new=2)

    def copy_lan_address() -> str | None:
        current = application.settings
        if current is None or not current.lan_enabled:
            return None
        value = lan_url(current.port)
        if value is None:
            return None
        window.clipboard_clear()
        window.clipboard_append(value)
        window.update_idletasks()
        return value

    update_window = None

    def open_update_details() -> bool:
        nonlocal update_window
        if update_window is not None and update_window.winfo_exists():
            update_window.lift()
            update_window.focus_force()
            return True
        if application.maintenance is None:
            return False
        update_window = UpdateDialog(
            window, controller=application.maintenance,
            on_check=application.check_updates, on_update=application.start_maintenance,
        )
        return True

    window = MainWindow(
        controller.logs,
        controller.journal,
        controller.progress,
        on_start=application.start,
        on_open_browser=open_comfy_browser,
        on_copy_lan=copy_lan_address,
        on_stop=application.stop,
        on_maintenance=application.start_maintenance,
        on_update_details=open_update_details,
        on_install=open_setup,
        on_cancel_maintenance=application.cancel_operation,
        state_provider=lambda: application.state,
        maintenance_running_provider=lambda: application.operation_running,
        installation_required_provider=lambda: application.installation_required,
        configured_provider=lambda: application.configured,
        update_available_provider=lambda: bool(
            application.maintenance
            and application.maintenance.available_plan
        ),
        lan_enabled_provider=lambda: bool(
            application.settings and application.settings.lan_enabled
        ),
        on_settings=open_settings,
        on_components=open_components,
        on_downloader=open_downloader,
    )
    window.set_profile_summary(_profile_summary(application.settings))
    window.after(350, application.check_updates)

    def apply_settings(updated) -> None:
        next_maintenance = _make_maintenance(
            updated.comfy_root, controller, operations
        )
        store.save(updated)
        diagnostics.set_secrets(
            (updated.civitai_token, updated.huggingface_token)
        )
        application.reconfigure(updated, next_maintenance)
        window.set_profile_summary(_profile_summary(updated))
        window.set_status("Готов к запуску", color="#2CBF59")

    def installation_succeeded(result, runtime, job) -> None:
        current = application.settings or LauncherSettings(
            comfy_root=result.target,
            venv_path=result.target / "venv",
        )
        updated = replace(
            current,
            comfy_root=result.target,
            venv_path=result.target / "venv",
            base_python=runtime.executable,
            update_channel=job.request.channel,
            torch_packages=job.torch_packages,
            torch_index_url=job.torch_index_url,
            torch_require_cuda=job.torch_expectation.require_cuda,
            torch_cuda_major=job.torch_expectation.cuda_major,
        )
        window.after(0, lambda: apply_settings(updated))

    installer.on_success = installation_succeeded

    def on_domain_event(event: DomainEvent) -> None:
        current = application.settings
        if (
            event.kind == "ServerReady"
            and current is not None
            and current.auto_open_browser
            and event.data.get("url")
        ):
            webbrowser.open(str(event.data["url"]), new=2)

    unsubscribe = controller.events.subscribe(on_domain_event)

    def close() -> None:
        nonlocal closing
        if closing:
            return
        closing = True
        unsubscribe()
        try:
            downloads.close()
        except Exception as exc:
            controller.logs.append(
                source="launcher",
                stream="error",
                text=f"Ошибка завершения загрузчика: {exc}\n",
            )
        try:
            application.close()
        except Exception as exc:
            controller.logs.append(
                source="launcher",
                stream="error",
                text=f"Ошибка завершения приложения: {exc}\n",
            )
        finally:
            diagnostics.close()
            window.destroy()

    window.protocol("WM_DELETE_WINDOW", close)
    if application.settings is None:
        window.after(120, open_setup)
    window.mainloop()
    return 0


def _default_install_target() -> Path | None:
    # A source checkout is not an installation directory, nor is PyInstaller's temp folder.
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None


def _make_environment_service() -> ComfyEnvironmentService:
    return ComfyEnvironmentService(
        VenvManager(),
        DependencySynchronizer(),
        RequirementsValidator(),
        TorchProfileValidator(),
    )


def _make_update_pipeline(root: Path, controller: ServerController) -> CoreUpdatePipeline:
    environment = _make_environment_service()
    return CoreUpdatePipeline(
        ComfyRepository(root),
        environment.environments,
        environment.dependencies,
        environment.requirements,
        environment.torch,
        server_is_stopped=lambda: controller.state in (ServerState.STOPPED, ServerState.FAILED),
    )


def _make_maintenance(
    root: Path,
    controller: ServerController,
    coordinator: OperationCoordinator,
) -> MaintenanceController:
    return MaintenanceController(
        _make_update_pipeline(root, controller),
        PythonResolver(),
        controller.logs,
        controller.journal,
        controller.progress,
        coordinator,
    )


def _profile_summary(settings) -> str:
    if settings is None:
        return "Профиль не настроен"
    profile = (
        f"CUDA {settings.torch_cuda_major}.x"
        if settings.torch_require_cuda and settings.torch_cuda_major is not None
        else "CPU"
    )
    lan = "вкл" if settings.lan_enabled else "выкл"
    return f"Профиль: {profile}  |  LAN: {lan}  |  Порт: {settings.port}"


def _open_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(path)  # type: ignore[attr-defined]
