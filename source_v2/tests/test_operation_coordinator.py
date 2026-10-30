from __future__ import annotations

import unittest
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from smart_launcher.application.installation_controller import (
    InstallationController,
    InstallationJob,
)
from smart_launcher.application.maintenance_controller import (
    MaintenanceController,
    MaintenanceRequest,
)
from smart_launcher.application.operation_coordinator import (
    OperationCoordinator,
    OperationKind,
)
from smart_launcher.domain.installation import (
    InstallationRequest,
    InstallationResult,
    InstallationStage,
)
from smart_launcher.domain.maintenance import MaintenanceResult, MaintenanceStage
from smart_launcher.domain.runtimes import RuntimeCandidate, RuntimeProbe, RuntimeSource
from smart_launcher.domain.updates import UpdateChannel
from smart_launcher.infrastructure.stores import EventJournal, LogStore, ProgressStore


class _Resolver:
    def resolve(self, _configured):
        path = Path("C:/Python313/python.exe")
        selected = RuntimeProbe(
            RuntimeCandidate(path, RuntimeSource.CONFIGURED), True, path
        )
        return SimpleNamespace(selected=selected, diagnostics=())


class _BlockingInstallPipeline:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def execute(self, request, *_args, **_kwargs):
        self.started.set()
        self.release.wait(2.0)
        return InstallationResult(
            True, InstallationStage.COMMIT, "ok", request.target, "commit"
        )


class _MaintenancePipeline:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, *_args, **_kwargs):
        self.calls += 1
        return MaintenanceResult(True, MaintenanceStage.COMMIT, "ok")


class OperationCoordinatorTests(unittest.TestCase):
    def test_only_one_mutating_operation_can_hold_a_lease(self) -> None:
        coordinator = OperationCoordinator()
        installation = coordinator.try_acquire(OperationKind.INSTALLATION)

        self.assertIsNotNone(installation)
        self.assertTrue(coordinator.busy)
        self.assertIsNone(coordinator.try_acquire(OperationKind.MAINTENANCE))
        self.assertEqual(coordinator.active.kind, OperationKind.INSTALLATION)

        installation.release()  # type: ignore[union-attr]
        maintenance = coordinator.try_acquire(OperationKind.MAINTENANCE)
        self.assertIsNotNone(maintenance)

    def test_release_is_idempotent_and_stale_release_cannot_clear_new_lease(self) -> None:
        coordinator = OperationCoordinator()
        first = coordinator.try_acquire(OperationKind.INSTALLATION)
        first.release()  # type: ignore[union-attr]
        second = coordinator.try_acquire(OperationKind.CUSTOM_NODES)

        first.release()  # type: ignore[union-attr]
        self.assertEqual(coordinator.active, second.snapshot)  # type: ignore[union-attr]
        second.release()  # type: ignore[union-attr]
        self.assertFalse(coordinator.busy)

    def test_shared_coordinator_blocks_other_controller_until_completion(self) -> None:
        coordinator = OperationCoordinator()
        stores = (LogStore(), EventJournal(), ProgressStore())
        install_pipeline = _BlockingInstallPipeline()
        maintenance_pipeline = _MaintenancePipeline()
        installation = InstallationController(
            install_pipeline,  # type: ignore[arg-type]
            _Resolver(),  # type: ignore[arg-type]
            *stores,
            coordinator=coordinator,
        )
        maintenance = MaintenanceController(
            maintenance_pipeline,  # type: ignore[arg-type]
            _Resolver(),  # type: ignore[arg-type]
            *stores,
            coordinator=coordinator,
        )
        try:
            self.assertTrue(
                installation.start(
                    InstallationJob(InstallationRequest(Path("D:/NewComfyUI")))
                )
            )
            self.assertTrue(install_pipeline.started.wait(1.0))
            self.assertFalse(
                maintenance.start(
                    MaintenanceRequest(UpdateChannel.STABLE, Path("D:/ComfyUI/venv"))
                )
            )
            self.assertEqual(maintenance_pipeline.calls, 0)

            install_pipeline.release.set()
            deadline = time.monotonic() + 2.0
            while coordinator.busy and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(coordinator.busy)
            self.assertTrue(
                maintenance.start(
                    MaintenanceRequest(UpdateChannel.STABLE, Path("D:/ComfyUI/venv"))
                )
            )
        finally:
            install_pipeline.release.set()
            installation.close()
            maintenance.close()


if __name__ == "__main__":
    unittest.main()
