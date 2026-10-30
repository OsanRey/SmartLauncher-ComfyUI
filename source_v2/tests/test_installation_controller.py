from __future__ import annotations

import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from smart_launcher.application.installation_controller import (
    InstallationController,
    InstallationJob,
)
from smart_launcher.domain.installation import (
    InstallationRequest,
    InstallationResult,
    InstallationStage,
)
from smart_launcher.domain.runtimes import RuntimeCandidate, RuntimeProbe, RuntimeSource
from smart_launcher.infrastructure.stores import EventJournal, LogStore, ProgressStore


def runtime() -> RuntimeProbe:
    path = Path("C:/Python313/python.exe")
    return RuntimeProbe(RuntimeCandidate(path, RuntimeSource.CONFIGURED), True, path)


class _Resolver:
    def __init__(self, selected=True) -> None:
        self.selected = selected

    def resolve(self, _configured):
        return SimpleNamespace(
            selected=runtime() if self.selected else None,
            diagnostics=("no trusted runtime",),
        )


class _Pipeline:
    def __init__(self, *, wait_for_cancel=False) -> None:
        self.wait_for_cancel = wait_for_cancel
        self.calls = 0

    def execute(self, request, _runtime, *, cancel_event=None, **_kwargs):
        self.calls += 1
        if self.wait_for_cancel:
            cancel_event.wait(2.0)
            return InstallationResult(
                False,
                InstallationStage.ROLLBACK,
                "cancelled",
                request.target,
                rolled_back=True,
            )
        return InstallationResult(
            True,
            InstallationStage.COMMIT,
            "installed",
            request.target,
            "commit",
        )


def wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class InstallationControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.logs = LogStore()
        self.events = EventJournal()
        self.progress = ProgressStore()

    def test_success_callback_runs_before_completion_event(self) -> None:
        callbacks = []
        pipeline = _Pipeline()
        controller = InstallationController(
            pipeline,  # type: ignore[arg-type]
            _Resolver(),  # type: ignore[arg-type]
            self.logs,
            self.events,
            self.progress,
            on_success=lambda result, selected, job: callbacks.append(
                (result, selected, job)
            ),
        )
        job = InstallationJob(InstallationRequest(Path("D:/NewComfyUI")))

        self.assertTrue(controller.start(job))
        self.assertTrue(wait_until(lambda: not controller.running))

        self.assertEqual(pipeline.calls, 1)
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(self.events.snapshot()[-1].event.kind, "InstallationCompleted")
        self.assertEqual(self.progress.snapshot(), ())

    def test_cancel_is_explicit_and_reaches_pipeline(self) -> None:
        pipeline = _Pipeline(wait_for_cancel=True)
        controller = InstallationController(
            pipeline,  # type: ignore[arg-type]
            _Resolver(),  # type: ignore[arg-type]
            self.logs,
            self.events,
            self.progress,
        )
        job = InstallationJob(InstallationRequest(Path("D:/NewComfyUI")))

        self.assertTrue(controller.start(job))
        self.assertTrue(wait_until(lambda: controller.running))
        self.assertFalse(controller.start(job))
        self.assertTrue(controller.cancel())
        self.assertTrue(wait_until(lambda: not controller.running))
        self.assertEqual(self.events.snapshot()[-1].event.kind, "InstallationFailed")

    def test_missing_runtime_does_not_touch_pipeline(self) -> None:
        pipeline = _Pipeline()
        controller = InstallationController(
            pipeline,  # type: ignore[arg-type]
            _Resolver(False),  # type: ignore[arg-type]
            self.logs,
            self.events,
            self.progress,
        )

        controller.start(InstallationJob(InstallationRequest(Path("D:/NewComfyUI"))))

        self.assertTrue(wait_until(lambda: not controller.running))
        self.assertEqual(pipeline.calls, 0)
        self.assertEqual(self.events.snapshot()[-1].event.kind, "InstallationFailed")


if __name__ == "__main__":
    unittest.main()
