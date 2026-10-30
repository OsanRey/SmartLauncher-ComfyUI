from __future__ import annotations

import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from smart_launcher.application.maintenance_controller import (
    MaintenanceController,
    MaintenanceRequest,
)
from smart_launcher.domain.maintenance import MaintenanceResult, MaintenanceStage
from smart_launcher.domain.maintenance import MaintenanceMode
from smart_launcher.domain.runtimes import RuntimeCandidate, RuntimeProbe, RuntimeSource
from smart_launcher.domain.updates import UpdateChannel
from smart_launcher.infrastructure.stores import EventJournal, LogStore, ProgressStore


def selected_runtime() -> RuntimeProbe:
    path = Path("C:/Python313/python.exe")
    return RuntimeProbe(RuntimeCandidate(path, RuntimeSource.CONFIGURED), True, path)


class _Resolver:
    def __init__(self, selected=True) -> None:
        self.selected = selected
        self.configured = None

    def resolve(self, configured):
        self.configured = tuple(configured)
        return SimpleNamespace(
            selected=selected_runtime() if self.selected else None,
            diagnostics=("no runtime",),
        )


class _Pipeline:
    def __init__(self, *, wait_for_cancel=False) -> None:
        self.wait_for_cancel = wait_for_cancel
        self.calls = 0
        self.repair_calls = 0
        self.check_calls = 0

    def execute(self, *_args, cancel_event=None, **_kwargs):
        self.calls += 1
        if self.wait_for_cancel:
            cancel_event.wait(2.0)
            return MaintenanceResult(False, MaintenanceStage.ROLLBACK, "cancelled")
        return MaintenanceResult(True, MaintenanceStage.COMMIT, "updated")

    def repair_environment(self, *_args, cancel_event=None, **_kwargs):
        self.repair_calls += 1
        return MaintenanceResult(True, MaintenanceStage.COMMIT, "repaired")

    def check_update(self, channel, **_kwargs):
        from smart_launcher.domain.updates import UpdatePlan

        self.check_calls += 1
        plan = UpdatePlan(
            Path("D:/ComfyUI"), channel, "old", "new", "refs/tags/v2"
        )
        return MaintenanceResult(True, MaintenanceStage.PLAN, "available", plan)


def wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class MaintenanceControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.logs = LogStore()
        self.events = EventJournal()
        self.progress = ProgressStore()

    def test_background_success_publishes_event_and_clears_progress(self) -> None:
        resolver = _Resolver()
        pipeline = _Pipeline()
        controller = MaintenanceController(
            pipeline, resolver, self.logs, self.events, self.progress  # type: ignore[arg-type]
        )

        started = controller.start(
            MaintenanceRequest(
                UpdateChannel.STABLE,
                Path("D:/ComfyUI/venv"),
                Path("C:/Python313/python.exe"),
            )
        )

        self.assertTrue(started)
        self.assertTrue(wait_until(lambda: not controller.running))
        self.assertEqual(pipeline.calls, 1)
        self.assertEqual(resolver.configured, (Path("C:/Python313/python.exe"),))
        self.assertEqual(self.events.snapshot()[0].event.title, "Обновление ComfyUI")
        self.assertEqual(self.events.snapshot()[-1].event.kind, "MaintenanceCompleted")
        self.assertEqual(self.progress.snapshot(), ())

    def test_second_start_is_rejected_and_cancel_reaches_pipeline(self) -> None:
        pipeline = _Pipeline(wait_for_cancel=True)
        controller = MaintenanceController(
            pipeline, _Resolver(), self.logs, self.events, self.progress  # type: ignore[arg-type]
        )
        request = MaintenanceRequest(UpdateChannel.STABLE, Path("D:/ComfyUI/venv"))

        self.assertTrue(controller.start(request))
        self.assertTrue(wait_until(lambda: controller.running))
        self.assertFalse(controller.start(request))
        self.assertTrue(controller.cancel())
        self.assertTrue(wait_until(lambda: not controller.running))
        self.assertEqual(pipeline.calls, 1)
        self.assertEqual(self.progress.snapshot(), ())

    def test_missing_runtime_finishes_without_calling_pipeline(self) -> None:
        pipeline = _Pipeline()
        controller = MaintenanceController(
            pipeline, _Resolver(False), self.logs, self.events, self.progress  # type: ignore[arg-type]
        )

        controller.start(MaintenanceRequest(UpdateChannel.STABLE, Path("D:/ComfyUI/venv")))

        self.assertTrue(wait_until(lambda: not controller.running))
        self.assertEqual(pipeline.calls, 0)
        self.assertEqual(self.events.snapshot()[-1].event.kind, "MaintenanceFailed")

    def test_environment_mode_calls_only_explicit_repair(self) -> None:
        pipeline = _Pipeline()
        controller = MaintenanceController(
            pipeline, _Resolver(), self.logs, self.events, self.progress  # type: ignore[arg-type]
        )
        request = MaintenanceRequest(
            UpdateChannel.STABLE,
            Path("D:/ComfyUI/venv"),
            mode=MaintenanceMode.ENVIRONMENT_REPAIR,
        )

        self.assertTrue(controller.start(request))
        self.assertTrue(wait_until(lambda: not controller.running))
        self.assertEqual(pipeline.calls, 0)
        self.assertEqual(pipeline.repair_calls, 1)

    def test_update_check_skips_python_resolution_and_reports_available(self) -> None:
        pipeline = _Pipeline()
        resolver = _Resolver(False)
        controller = MaintenanceController(
            pipeline, resolver, self.logs, self.events, self.progress  # type: ignore[arg-type]
        )
        request = MaintenanceRequest(
            UpdateChannel.STABLE,
            Path("D:/ComfyUI/venv"),
            mode=MaintenanceMode.UPDATE_CHECK,
        )

        self.assertTrue(controller.start(request))
        self.assertTrue(wait_until(lambda: not controller.running))
        self.assertEqual(pipeline.check_calls, 1)
        self.assertEqual(self.events.snapshot()[0].event.title, "Автоматическая проверка обновлений ComfyUI")
        self.assertIsNone(resolver.configured)
        event = self.events.snapshot()[-1].event
        self.assertEqual(event.kind, "UpdateAvailable")
        self.assertEqual(event.severity.value, "warning")


if __name__ == "__main__":
    unittest.main()
