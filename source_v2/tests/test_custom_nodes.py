from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from smart_launcher.application.custom_node_controller import CustomNodeController
from smart_launcher.application.custom_node_pipeline import CustomNodePipeline
from smart_launcher.application.operation_coordinator import OperationCoordinator, OperationKind
from smart_launcher.domain.commands import CommandFailure
from smart_launcher.domain.custom_nodes import (
    CustomNodeRequest,
    parse_github_origin,
    parse_github_repository,
)
from smart_launcher.infrastructure.stores import EventJournal, LogStore, ProgressStore


class _Runner:
    def __init__(
        self,
        *,
        origin: str = "https://github.com/owner/node.git",
        dirty: bool = False,
        fail_source: str | None = None,
    ) -> None:
        self.origin = origin
        self.dirty = dirty
        self.specs = []
        self.add_requirements = False
        self.fail_source = fail_source

    def run(self, spec, *, on_chunk=None, cancel_event=None):
        self.specs.append(spec)
        argv = spec.argv
        stdout = ""
        if argv[:3] == ("git", "remote", "get-url"):
            stdout = self.origin + "\n"
        elif argv[:3] == ("git", "status", "--porcelain"):
            stdout = " M local.py\n" if self.dirty else ""
        elif argv[:2] == ("git", "clone"):
            target = Path(argv[-1])
            (target / ".git").mkdir(parents=True)
            if self.add_requirements:
                (target / "requirements.txt").write_text("sample-package>=1\n", encoding="utf-8")
        if cancel_event is not None and cancel_event.is_set():
            return self._result(False, CommandFailure.CANCELLED)
        if spec.source == self.fail_source:
            return self._result(False, CommandFailure.NONZERO_EXIT)
        if on_chunk is not None:
            on_chunk(SimpleNamespace(source=spec.source, stream="stdout", text="ok\n"))
        return self._result(True, CommandFailure.NONE, stdout)

    @staticmethod
    def _result(succeeded, failure, stdout=""):
        return SimpleNamespace(
            succeeded=succeeded,
            failure=failure,
            stdout=stdout,
            stderr="",
            exception=None,
        )


class _BlockingPipeline:
    def __init__(self) -> None:
        self.started = threading.Event()

    def execute(self, request, *, cancel_event, **_kwargs):
        self.started.set()
        cancel_event.wait(2.0)
        return SimpleNamespace(
            succeeded=False,
            cancelled=True,
            detail="Операция отменена.",
            target=None,
        )


class CustomNodeTests(unittest.TestCase):
    def test_github_parser_accepts_repository_and_rejects_subpages(self) -> None:
        repository = parse_github_repository("https://github.com/Owner/Node.git/")
        self.assertEqual(repository.clone_url, "https://github.com/Owner/Node.git")
        self.assertEqual(
            parse_github_origin("git@github.com:Owner/Node.git").clone_url,
            repository.clone_url,
        )
        for value in (
            "http://github.com/owner/node",
            "https://example.com/owner/node",
            "https://github.com/owner/node/tree/main",
            "https://user:secret@github.com/owner/node",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_github_repository(value)

    def test_fresh_node_is_staged_then_installed_and_requirements_use_venv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, venv = self._installation(Path(directory))
            stale = root / "custom_nodes" / ".smart-launcher-stage-node-abandoned"
            stale.mkdir(parents=True)
            (stale / "partial.pack").write_bytes(b"partial")
            runner = _Runner()
            runner.add_requirements = True
            result = CustomNodePipeline(runner, server_is_stopped=lambda: True).execute(
                CustomNodeRequest("https://github.com/owner/node", root, venv)
            )

            self.assertTrue(result.succeeded, result.detail)
            self.assertEqual(result.target, root / "custom_nodes" / "node")
            self.assertTrue((result.target / ".git").is_dir())  # type: ignore[union-attr]
            pip = next(spec for spec in runner.specs if spec.source == "custom-node-pip")
            self.assertEqual(Path(pip.argv[0]), self._python(venv))
            self.assertEqual(pip.argv[1:4], ("-m", "pip", "install"))
            clone = next(spec for spec in runner.specs if spec.source == "custom-node-git")
            self.assertIn("--progress", clone.argv)
            self.assertEqual(clone.idle_timeout, 60)
            self.assertEqual(clone.env["GIT_TERMINAL_PROMPT"], "0")
            self.assertFalse(any((root / "custom_nodes").glob(".smart-launcher-*")))

    def test_dirty_existing_node_is_left_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, venv = self._installation(Path(directory))
            target = root / "custom_nodes" / "node"
            (target / ".git").mkdir(parents=True)
            marker = target / "local.py"
            marker.write_text("mine", encoding="utf-8")
            runner = _Runner(dirty=True)

            result = CustomNodePipeline(runner, server_is_stopped=lambda: True).execute(
                CustomNodeRequest("https://github.com/owner/node", root, venv)
            )

            self.assertFalse(result.succeeded)
            self.assertIn("локальные изменения", result.detail)
            self.assertEqual(marker.read_text(encoding="utf-8"), "mine")
            self.assertFalse(any(spec.source == "custom-node-git" for spec in runner.specs))

    def test_dependency_failure_keeps_existing_node_and_removes_staging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, venv = self._installation(Path(directory))
            target = root / "custom_nodes" / "node"
            (target / ".git").mkdir(parents=True)
            marker = target / "current.py"
            marker.write_text("working", encoding="utf-8")
            runner = _Runner(fail_source="custom-node-pip")
            runner.add_requirements = True

            result = CustomNodePipeline(runner, server_is_stopped=lambda: True).execute(
                CustomNodeRequest("https://github.com/owner/node", root, venv)
            )

            self.assertFalse(result.succeeded)
            self.assertEqual(marker.read_text(encoding="utf-8"), "working")
            self.assertFalse(any((root / "custom_nodes").glob(".smart-launcher-*")))

    def test_server_guard_runs_before_git(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, venv = self._installation(Path(directory))
            runner = _Runner()
            result = CustomNodePipeline(runner, server_is_stopped=lambda: False).execute(
                CustomNodeRequest("https://github.com/owner/node", root, venv)
            )
            self.assertFalse(result.succeeded)
            self.assertEqual(runner.specs, [])

    def test_registry_update_uses_manager_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, venv = self._installation(Path(directory))
            cli = venv / ("Scripts/cm-cli.exe" if os.name == "nt" else "bin/cm-cli")
            cli.write_bytes(b"")
            runner = _Runner()

            result = CustomNodePipeline(
                runner, server_is_stopped=lambda: True
            ).execute(
                CustomNodeRequest(
                    "https://github.com/owner/node",
                    root,
                    venv,
                    registry_id="registry-node",
                )
            )

            self.assertTrue(result.succeeded, result.detail)
            spec = runner.specs[0]
            self.assertEqual(spec.argv, (str(cli), "update", "registry-node"))
            self.assertEqual(spec.env["COMFYUI_PATH"], str(root.resolve()))

    def test_dependency_repair_uses_manager_fix_all(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, venv = self._installation(Path(directory))
            cli = venv / ("Scripts/cm-cli.exe" if os.name == "nt" else "bin/cm-cli")
            cli.write_bytes(b"")
            runner = _Runner()

            result = CustomNodePipeline(
                runner, server_is_stopped=lambda: True
            ).execute(CustomNodeRequest("", root, venv, repair_dependencies=True))

            self.assertTrue(result.succeeded, result.detail)
            self.assertEqual(runner.specs[0].argv, (str(cli), "fix", "all"))
            self.assertEqual(runner.specs[0].source, "custom-node-dependencies")
            self.assertEqual(runner.specs[0].timeout, 3600)
            self.assertIsNone(runner.specs[0].idle_timeout)

    def test_remove_manual_node_and_reject_outside_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, venv = self._installation(Path(directory))
            node = root / "custom_nodes" / "manual-node"
            node.mkdir(parents=True)
            (node / "file.py").write_text("pass", encoding="utf-8")
            pipeline = CustomNodePipeline(_Runner(), server_is_stopped=lambda: True)

            removed = pipeline.execute(
                CustomNodeRequest("", root, venv, remove_target=node)
            )
            outside = root / "keep-me"
            outside.mkdir()
            rejected = pipeline.execute(
                CustomNodeRequest("", root, venv, remove_target=outside)
            )

            self.assertTrue(removed.succeeded, removed.detail)
            self.assertFalse(node.exists())
            self.assertFalse(rejected.succeeded)
            self.assertTrue(outside.exists())

    def test_shared_custom_nodes_directory_symlink_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root, venv = self._installation(base)
            shared = base / "shared-custom-nodes"
            shared.mkdir()
            try:
                os.symlink(shared, root / "custom_nodes", target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlinks are unavailable: {exc}")

            result = CustomNodePipeline(
                _Runner(), server_is_stopped=lambda: True
            ).execute(CustomNodeRequest("https://github.com/owner/node", root, venv))

            self.assertTrue(result.succeeded, result.detail)
            self.assertEqual(result.target, shared.resolve() / "node")
            self.assertTrue((shared / "node" / ".git").is_dir())

    def test_individual_node_symlink_remains_protected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root, venv = self._installation(base)
            custom_nodes = root / "custom_nodes"
            custom_nodes.mkdir()
            external = base / "external-node"
            external.mkdir()
            try:
                os.symlink(external, custom_nodes / "node", target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlinks are unavailable: {exc}")

            runner = _Runner()
            result = CustomNodePipeline(
                runner, server_is_stopped=lambda: True
            ).execute(CustomNodeRequest("https://github.com/owner/node", root, venv))

            self.assertFalse(result.succeeded)
            self.assertIn("отдельной Custom Node", result.detail)
            self.assertEqual(runner.specs, [])
            self.assertFalse((external / ".git").exists())

    def test_controller_uses_coordinator_and_explicit_cancel(self) -> None:
        coordinator = OperationCoordinator()
        events = EventJournal()
        controller = CustomNodeController(
            _BlockingPipeline(),  # type: ignore[arg-type]
            LogStore(),
            events,
            ProgressStore(),
            coordinator,
        )
        request = CustomNodeRequest(
            "https://github.com/owner/node", Path("D:/ComfyUI"), Path("D:/ComfyUI/venv")
        )
        try:
            self.assertTrue(controller.start(request))
            self.assertTrue(controller.pipeline.started.wait(1.0))  # type: ignore[attr-defined]
            self.assertIsNone(coordinator.try_acquire(OperationKind.MAINTENANCE))
            self.assertTrue(controller.cancel())
            deadline = time.monotonic() + 2.0
            while controller.running and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(controller.running)
            self.assertFalse(coordinator.busy)
            self.assertEqual(events.snapshot()[-1].event.kind, "CustomNodeCancelled")
        finally:
            controller.close()

    @staticmethod
    def _installation(base: Path) -> tuple[Path, Path]:
        root = base / "ComfyUI"
        root.mkdir()
        (root / "main.py").write_text("", encoding="utf-8")
        venv = root / "venv"
        python = CustomNodeTests._python(venv)
        python.parent.mkdir(parents=True)
        python.write_bytes(b"")
        return root, venv

    @staticmethod
    def _python(venv: Path) -> Path:
        return (venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")).resolve()


if __name__ == "__main__":
    unittest.main()
