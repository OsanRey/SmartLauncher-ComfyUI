from pathlib import Path
import os
import unittest
from types import SimpleNamespace

from smart_launcher.application.maintenance_controller import MaintenanceController, MaintenanceRequest
from smart_launcher.domain.maintenance import MaintenanceMode, MaintenanceResult, MaintenanceStage
from smart_launcher.domain.updates import UpdateChannel, UpdatePlan
from smart_launcher.infrastructure.stores import LogStore, EventJournal, ProgressStore


class UpdatePreviewTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("SMART_LAUNCHER_UI_TEST") == "1", "desktop UI opt-in")
    def test_update_and_details_are_separate_actions(self):
        import customtkinter as ctk
        from smart_launcher.domain.events import DomainEvent
        from smart_launcher.ui.main_window import MainWindow
        events = EventJournal()
        events.append(DomainEvent("UpdateAvailable", "Update available"))
        calls = []
        window = MainWindow(
            LogStore(), events, ProgressStore(),
            on_maintenance=lambda: calls.append("update") or True,
            on_update_details=lambda: calls.append("details") or True,
        )
        window.withdraw()
        try:
            window.maintenance_button.invoke()
            self.assertEqual(calls, ["update"])
            window._drain_projection()
            buttons = [child for row in window.event_view._rows
                       for child in row.winfo_children() if isinstance(child, ctk.CTkButton)]
            self.assertEqual(len(buttons), 1)
            buttons[0].invoke()
            self.assertEqual(calls, ["update", "details"])
        finally:
            for timer in window.tk.call("after", "info"):
                window.tk.call("after", "cancel", timer)
            window.destroy()

    def test_plain_summary_keeps_technical_data_out(self):
        from smart_launcher.ui.update_dialog import update_summary
        plan = UpdatePlan(Path("example"), UpdateChannel.STABLE, "old", "new", "v2",
                          changes=("abc123 Fix loading", "def456 Update frontend"))
        summary = update_summary(plan)
        self.assertIn("исправления ошибок", summary)
        self.assertIn("веб-интерфейс", summary)
        self.assertIn("установка пакетов не требуется", summary)
        self.assertNotIn("abc123", summary)

    def test_changed_target_is_rejected_before_environment_changes(self):
        from unittest.mock import Mock
        from smart_launcher.application.update_pipeline import CoreUpdatePipeline
        approved = UpdatePlan(Path("example"), UpdateChannel.STABLE, "old", "new", "v2")
        newer = UpdatePlan(Path("example"), UpdateChannel.STABLE, "old", "newer", "v3")
        repository = Mock()
        repository.root = Path("example")
        repository.prepare.return_value = newer
        environments = Mock()
        pipeline = CoreUpdatePipeline(
            repository, environments, Mock(), Mock(), Mock(), server_is_stopped=lambda: True,
        )
        result = pipeline.execute(UpdateChannel.STABLE, Path("venv"), Mock(), approved_plan=approved)
        self.assertFalse(result.succeeded)
        self.assertEqual(result.stage, MaintenanceStage.PLAN)
        repository.apply.assert_not_called()
        environments.inspect.assert_not_called()

    def test_available_plan_clears_after_success_not_failure_or_repair(self):
        controller = MaintenanceController(None, None, LogStore(), EventJournal(), ProgressStore())
        plan = UpdatePlan(Path("example"), UpdateChannel.STABLE, "old", "new", "refs/tags/v2")
        def publish(mode, success, result_plan=plan):
            controller._publish_result(
                MaintenanceRequest(UpdateChannel.STABLE, Path("venv"), mode=mode),
                "test", MaintenanceResult(success, MaintenanceStage.COMMIT, "test", result_plan),
            )
        publish(MaintenanceMode.UPDATE_CHECK, True)
        self.assertEqual(controller.available_plan, plan)
        publish(MaintenanceMode.ENVIRONMENT_REPAIR, True, None)
        self.assertEqual(controller.available_plan, plan)
        publish(MaintenanceMode.CORE_UPDATE, False)
        self.assertEqual(controller.available_plan, plan)
        publish(MaintenanceMode.CORE_UPDATE, True)
        self.assertIsNone(controller.available_plan)
        self.assertTrue(controller.last_result.plan.update_available)

    def test_current_check_clears_available_plan(self):
        controller = MaintenanceController(None, None, LogStore(), EventJournal(), ProgressStore())
        plan = UpdatePlan(Path("example"), UpdateChannel.STABLE, "new", "new", "refs/tags/v2")
        controller._publish_result(
            MaintenanceRequest(UpdateChannel.STABLE, Path("venv"), mode=MaintenanceMode.UPDATE_CHECK),
            "test", MaintenanceResult(True, MaintenanceStage.PLAN, "current", plan),
        )
        self.assertIsNone(controller.available_plan)

    @unittest.skipUnless(os.environ.get("SMART_LAUNCHER_UI_TEST") == "1", "desktop UI opt-in")
    def test_dialog_preview_and_confirmation(self):
        import customtkinter as ctk
        from smart_launcher.ui.update_dialog import UpdateDialog
        root = ctk.CTk()
        root.withdraw()
        plan = UpdatePlan(
            Path("example"), UpdateChannel.STABLE, "old", "new", "refs/tags/v2",
            ("requirements.txt",), "v1", ("abc Fix loading",), "-package==1\n+package==2",
        )
        controller = SimpleNamespace(available_plan=plan, last_result=None, running=False)
        called = []
        dialog = UpdateDialog(root, controller=controller, on_check=lambda: False,
                              on_update=lambda: called.append(True) or True)
        try:
            root.update()
            self.assertIn("Fix loading", dialog.content.get("1.0", "end"))
            self.assertEqual(dialog.tabs.get(), "Кратко")
            self.assertNotIn("abc Fix loading", dialog.summary.get("1.0", "end"))
            self.assertIn("v1", dialog.versions.cget("text"))
            self.assertEqual(dialog.install.cget("state"), "normal")
            self.assertEqual(called, [])
            dialog._install()
            self.assertEqual(called, [True])
        finally:
            for timer in root.tk.call("after", "info"):
                root.tk.call("after", "cancel", timer)
            root.destroy()
