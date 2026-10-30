from __future__ import annotations

import os
import time
import unittest
from pathlib import Path

from smart_launcher.domain.events import DomainEvent
from smart_launcher.infrastructure.stores import EventJournal, LogStore, ProgressStore


@unittest.skipUnless(
    os.environ.get("SMART_LAUNCHER_UI_TEST") == "1",
    "requires an interactive Windows desktop",
)
class MainWindowSmokeTests(unittest.TestCase):
    def test_single_root_ui_lifecycle(self) -> None:
        from smart_launcher.domain.comfy import ServerState
        from smart_launcher.domain.updates import UpdateChannel
        from smart_launcher.ui.install_wizard import InstallWizard
        from smart_launcher.ui.main_window import MainWindow
        from smart_launcher.ui.settings_dialog import SettingsDialog
        from smart_launcher.ui.setup_dialog import SetupDialog
        from smart_launcher.ui.downloader_window import DownloaderWindow
        from smart_launcher.application.download_controller import DownloadController
        from smart_launcher.application.custom_node_controller import CustomNodeController
        from smart_launcher.application.custom_node_catalog import CustomNodeCatalog
        from smart_launcher.application.custom_node_pipeline import CustomNodePipeline
        from smart_launcher.application.operation_coordinator import OperationCoordinator
        from smart_launcher.infrastructure.command_runner import CommandRunner
        from smart_launcher.downloads.resolver import ModelLinkResolver
        from smart_launcher.downloads.transport import ModelDownloadTransport
        from smart_launcher.domain.settings import LauncherSettings

        logs = LogStore()
        events = EventJournal()
        progress = ProgressStore()
        logs.append(source="test", stream="stdout", text="console-line\n")
        events.append(DomainEvent("Ready", "Событие сохранено"))
        state = {
            "installation_required": True,
            "configured": True,
            "update_available": False,
            "server": ServerState.STOPPED,
            "lan": False,
        }
        opened = []
        copied = []
        components_opened = []
        window = MainWindow(
            logs,
            events,
            progress,
            on_start=lambda: None,
            on_open_browser=lambda: opened.append(True),
            on_copy_lan=lambda: copied.append(True) or "http://192.168.1.2:8188",
            on_maintenance=lambda: True,
            on_install=lambda: None,
            state_provider=lambda: state["server"],
            maintenance_running_provider=lambda: False,
            installation_required_provider=lambda: state["installation_required"],
            configured_provider=lambda: state["configured"],
            update_available_provider=lambda: state["update_available"],
            lan_enabled_provider=lambda: state["lan"],
            on_components=lambda: components_opened.append(True),
        )
        window.withdraw()
        try:
            window._refresh_server_state()
            self.assertEqual(window.maintenance_button.cget("text"), "Установить")
            self.assertEqual(window.start_button.cget("state"), "disabled")
            self.assertEqual(window.components_button.cget("text"), "Система")
            window.components_button.invoke()
            self.assertEqual(components_opened, [True])

            state["installation_required"] = False
            window._refresh_maintenance_state(force=True)
            self.assertEqual(window.maintenance_button.cget("text"), "Обновить")
            self.assertEqual(window.start_button.cget("state"), "normal")

            state["update_available"] = True
            window._refresh_maintenance_state(force=True)
            self.assertEqual(window.maintenance_button.cget("text"), "Обновить •")

            state["server"] = ServerState.READY
            window._refresh_server_state()
            self.assertEqual(window.start_button.cget("text"), "Открыть в браузере")
            self.assertEqual(window.start_button.grid_info()["columnspan"], 2)
            self.assertFalse(window.copy_lan_button.winfo_ismapped())
            window.start_button.invoke()
            self.assertEqual(opened, [True])

            state["lan"] = True
            window._refresh_server_state()
            window.update_idletasks()
            self.assertEqual(window.start_button.cget("text"), "Открыть")
            self.assertEqual(window.start_button.grid_info()["columnspan"], 1)
            self.assertTrue(window.copy_lan_button.grid_info())
            self.assertEqual(window.launch_actions.cget("width"), 150)
            window.copy_lan_button.invoke()
            self.assertEqual(copied, [True])

            time.sleep(0.1)
            window.update()
            self.assertEqual(len(window.event_view._rows), 1)
            self.assertIn("console-line", window.console_view.text.get("1.0", "end"))

            events.append(DomainEvent("ConsoleNotice", "Test warning", detail="pip install example"))
            time.sleep(0.06)
            window.update()
            self.assertEqual(len(window.event_view._rows), 1)
            self.assertEqual(len(window.notice_view._rows), 1)
            self.assertTrue(window._unread_notices)
            self.assertIn("Предупреждения •", window.tab_switch.cget("values"))
            self.assertEqual(window.tab_switch.get(), "События")
            window.show_tab("Предупреждения")
            self.assertFalse(window._unread_notices)
            self.assertFalse(window.event_count_label.grid_info())
            events.append(DomainEvent("ConsoleNotice", "Second warning"))
            time.sleep(0.06)
            window.update()
            self.assertFalse(window._unread_notices)
            self.assertEqual(len(window.notice_view._rows), 2)
            window.show_tab("События")
            events.append(DomainEvent("ConsoleNotice", "Third warning"))
            time.sleep(0.06)
            window.update()
            self.assertTrue(window._unread_notices)
            window._show_tab("Предупреждения •")
            self.assertEqual(window.tab_switch.get(), "Предупреждения")
            self.assertEqual(len(window.notice_view._rows), 3)
            import tkinter as tk
            fields = [child for child in window.notice_view._rows[0].winfo_children()
                      if isinstance(child, tk.Text)]
            self.assertEqual(len(fields), 2)
            detail = fields[1]
            original = detail.get("1.0", "end-1c")
            detail.insert("end", "must not be inserted")
            self.assertEqual(detail.get("1.0", "end-1c"), original)
            detail.tag_add("sel", "1.0", "1.3")
            detail.event_generate("<Control-c>")
            detail._copy()
            self.assertEqual(window.clipboard_get(), "pip")
            detail._select_all()
            detail._copy()
            self.assertEqual(window.clipboard_get(), original)

            window.show_tab("Консоль")
            window.deiconify()
            window.update()
            window.update_idletasks()
            self.assertFalse(window.console_view._scroll_pending)
            window.show_tab("События")
            window.update_idletasks()

            self.assertEqual(len(window.event_view._rows), 1)
            self.assertIn("console-line", window.console_view.text.get("1.0", "end"))

            submitted = []
            wizard = InstallWizard(
                window,
                initial_target=Path("D:/FreshComfyUI"),
                initial_python=None,
                initial_channel=UpdateChannel.STABLE,
                on_install=lambda job: submitted.append(job) or True,
            )
            wizard.withdraw()
            wizard.update()
            time.sleep(0.25)
            wizard.update()
            wizard.profile.set(wizard.CPU_PROFILE)
            wizard._next()
            self.assertEqual(wizard._page, 1)
            self.assertEqual(submitted, [])

            wizard._next()
            self.assertEqual(len(submitted), 1)
            self.assertFalse(submitted[0].torch_expectation.require_cuda)
            self.assertIsNone(submitted[0].torch_index_url)

            setup = SetupDialog(
                window,
                on_new_install=lambda: None,
                on_connect=lambda *_args: None,  # type: ignore[arg-type]
                on_connected=lambda _profile: None,
            )
            setup.withdraw()
            setup.update_idletasks()
            self.assertEqual(setup.title(), "Настройка Smart Launcher 2.0")
            setup.destroy()

            saved = []
            settings = SettingsDialog(
                window,
                LauncherSettings(Path("D:/IndependentComfy"), Path("D:/IndependentComfy/venv")),
                on_save=saved.append,
            )
            settings.withdraw()
            settings.update_idletasks()
            settings.port.delete(0, "end")
            settings.port.insert(0, "8288")
            settings._save()
            self.assertEqual(saved[0].port, 8288)

            download_controller = DownloadController(
                ModelDownloadTransport(), logs, events, progress
            )
            custom_node_controller = CustomNodeController(
                CustomNodePipeline(CommandRunner(), server_is_stopped=lambda: True),
                logs,
                events,
                progress,
                OperationCoordinator(),
            )
            downloader = DownloaderWindow(
                window,
                LauncherSettings(
                    Path("D:/IndependentComfy"), Path("D:/IndependentComfy/venv")
                ),
                download_controller,
                ModelLinkResolver(),
                progress,
                custom_node_controller,
                CustomNodeCatalog(CommandRunner()),
                on_tokens_changed=lambda *_args: None,
                on_custom_node_start=lambda request: custom_node_controller.start(request),
                on_custom_node_cancel=custom_node_controller.cancel,
            )
            downloader.withdraw()
            downloader.update_idletasks()
            self.assertIn(
                "models\\diffusion_models",
                downloader.destination.cget("text"),
            )
            downloader.tabs.set("Custom Nodes")
            downloader.update_idletasks()
            self.assertEqual(downloader.node_button.cget("text"), "Установить или обновить")
            downloader.destroy()
            download_controller.close()
            custom_node_controller.close()
        finally:
            window.destroy()


if __name__ == "__main__":
    unittest.main()
