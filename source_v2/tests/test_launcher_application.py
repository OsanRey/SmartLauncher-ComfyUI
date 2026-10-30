from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from smart_launcher.application.launcher import LauncherApplication
from smart_launcher.domain.comfy import ServerState
from smart_launcher.domain.settings import LauncherSettings
from smart_launcher.infrastructure.stores import EventJournal


class _FakeController:
    def __init__(self) -> None:
        self.state = ServerState.STOPPED
        self.journal = EventJournal()
        self.launch = None
        self.stop_calls = 0
        self.failure: Exception | None = None

    def start(self, launch) -> None:
        if self.failure is not None:
            raise self.failure
        self.launch = launch
        self.state = ServerState.STARTING

    def stop(self) -> bool:
        self.stop_calls += 1
        self.state = ServerState.STOPPED
        return True


class _FakeMaintenance:
    available_plan = None
    def __init__(self) -> None:
        self.running = False
        self.starts = 0
        self.cancels = 0
        self.closes = 0
        self.request = None

    def start(self, request) -> bool:
        self.starts += 1
        self.request = request
        self.running = True
        return True

    def cancel(self) -> bool:
        self.cancels += 1
        self.running = False
        return True

    def close(self) -> None:
        self.closes += 1


class _FakeCustomNodes:
    def __init__(self) -> None:
        self.running = False
        self.starts = 0
        self.cancels = 0

    def start(self, _request) -> bool:
        self.starts += 1
        self.running = True
        return True

    def cancel(self) -> bool:
        self.cancels += 1
        self.running = False
        return True

    def close(self) -> None:
        self.running = False


class LauncherApplicationTests(unittest.TestCase):
    def test_builds_server_launch_only_from_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ComfyUI"
            root.mkdir()
            (root / "main.py").write_text("", encoding="utf-8")
            venv = root / "venv"
            (venv / "Scripts").mkdir(parents=True)
            (venv / "Scripts" / "python.exe").write_text("", encoding="utf-8")
            (venv / "pyvenv.cfg").write_text("home = C:/Python313\n", encoding="utf-8")
            settings = LauncherSettings(
                root,
                root / "venv",
                port=8288,
                lan_enabled=True,
                manager_enabled=True,
                extra_arguments=("--preview-method", "auto"),
            )
            controller = _FakeController()
            application = LauncherApplication(settings, controller)  # type: ignore[arg-type]

            application.start()

            self.assertEqual(controller.launch.root, root)
            self.assertEqual(controller.launch.host, "0.0.0.0")
            self.assertEqual(controller.launch.port, 8288)
            self.assertEqual(
                controller.launch.arguments,
                ("--enable-manager", "--preview-method", "auto"),
            )

    def test_start_failure_is_recorded_as_domain_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "ComfyUI"
            root.mkdir()
            (root / "main.py").write_text("", encoding="utf-8")
            venv = root / "venv"
            (venv / "Scripts").mkdir(parents=True)
            (venv / "Scripts" / "python.exe").write_text("", encoding="utf-8")
            (venv / "pyvenv.cfg").write_text("home = C:/Python313\n", encoding="utf-8")
            controller = _FakeController()
            controller.failure = FileNotFoundError("missing venv")
            application = LauncherApplication(
                LauncherSettings(root, root / "venv"),
                controller,  # type: ignore[arg-type]
            )

            with self.assertRaises(FileNotFoundError):
                application.start()

            entries = controller.journal.snapshot()
            self.assertEqual(entries[-1].event.kind, "ServerStartRejected")
            self.assertIn("missing venv", entries[-1].event.detail or "")

    def test_close_stops_only_an_active_controller(self) -> None:
        controller = _FakeController()
        application = LauncherApplication(
            LauncherSettings(Path("D:/ComfyUI"), Path("D:/ComfyUI/venv")),
            controller,  # type: ignore[arg-type]
        )
        application.close()
        self.assertEqual(controller.stop_calls, 0)

        controller.state = ServerState.READY
        application.close()
        self.assertEqual(controller.stop_calls, 1)

        controller.state = ServerState.FAILED
        application.close()
        self.assertEqual(controller.stop_calls, 2)

    def test_unconfigured_application_cannot_start_or_update(self) -> None:
        controller = _FakeController()
        application = LauncherApplication(None, controller)  # type: ignore[arg-type]

        self.assertTrue(application.installation_required)
        self.assertFalse(application.configured)
        self.assertFalse(application.start_maintenance())
        with self.assertRaises(RuntimeError):
            application.start()
        self.assertEqual(controller.journal.snapshot()[-1].event.kind, "ServerStartRejected")

    def test_server_and_maintenance_are_mutually_exclusive(self) -> None:
        controller = _FakeController()
        maintenance = _FakeMaintenance()
        application = LauncherApplication(
            LauncherSettings(Path("D:/ComfyUI"), Path("D:/ComfyUI/venv")),
            controller,  # type: ignore[arg-type]
            maintenance,  # type: ignore[arg-type]
        )

        self.assertTrue(application.start_maintenance())
        with self.assertRaises(RuntimeError):
            application.start()
        application.cancel_maintenance()

        controller.state = ServerState.READY
        self.assertFalse(application.start_maintenance())
        self.assertEqual(maintenance.starts, 1)

    def test_environment_repair_is_an_explicit_maintenance_mode(self) -> None:
        from smart_launcher.domain.maintenance import MaintenanceMode

        controller = _FakeController()
        maintenance = _FakeMaintenance()
        application = LauncherApplication(
            LauncherSettings(Path("D:/ComfyUI"), Path("D:/ComfyUI/venv")),
            controller,  # type: ignore[arg-type]
            maintenance,  # type: ignore[arg-type]
        )

        self.assertTrue(application.start_environment_repair())
        self.assertEqual(maintenance.request.mode, MaintenanceMode.ENVIRONMENT_REPAIR)

    def test_update_check_uses_read_only_mode(self) -> None:
        from smart_launcher.domain.maintenance import MaintenanceMode

        controller = _FakeController()
        maintenance = _FakeMaintenance()
        application = LauncherApplication(
            LauncherSettings(Path("D:/ComfyUI"), Path("D:/ComfyUI/venv")),
            controller,  # type: ignore[arg-type]
            maintenance,  # type: ignore[arg-type]
        )

        self.assertTrue(application.check_updates())
        self.assertEqual(maintenance.request.mode, MaintenanceMode.UPDATE_CHECK)

    def test_custom_nodes_are_rejected_while_server_is_active(self) -> None:
        from smart_launcher.domain.custom_nodes import CustomNodeRequest

        controller = _FakeController()
        custom_nodes = _FakeCustomNodes()
        application = LauncherApplication(
            LauncherSettings(Path("D:/ComfyUI"), Path("D:/ComfyUI/venv")),
            controller,  # type: ignore[arg-type]
            custom_nodes=custom_nodes,  # type: ignore[arg-type]
        )
        request = CustomNodeRequest(
            "https://github.com/owner/node",
            Path("D:/ComfyUI"),
            Path("D:/ComfyUI/venv"),
        )

        controller.state = ServerState.READY
        self.assertFalse(application.start_custom_node(request))
        self.assertEqual(custom_nodes.starts, 0)
        controller.state = ServerState.STOPPED
        self.assertTrue(application.start_custom_node(request))
        self.assertTrue(application.cancel_operation())
        self.assertEqual(custom_nodes.cancels, 1)


if __name__ == "__main__":
    unittest.main()
