from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from smart_launcher.domain.commands import CommandFailure, CommandResult
from smart_launcher.domain.environments import EnvironmentState, RepairStage
from smart_launcher.domain.runtimes import RuntimeCandidate, RuntimeProbe, RuntimeSource
from smart_launcher.runtime.venv_manager import VenvManager


def command_result(spec, *, success: bool = True, stderr: str = "") -> CommandResult:
    now = datetime.now(UTC)
    return CommandResult(
        spec=spec,
        failure=CommandFailure.NONE if success else CommandFailure.NONZERO_EXIT,
        return_code=0 if success else 1,
        started_at=now,
        finished_at=now,
        stdout="pip 26.0" if success else "",
        stderr=stderr,
    )


def runtime_probe(path: Path, *, is_venv: bool) -> RuntimeProbe:
    candidate = RuntimeCandidate(path, RuntimeSource.CONFIGURED)
    return RuntimeProbe(
        candidate=candidate,
        valid=True,
        executable=path,
        version="3.13.15",
        version_info=(3, 13, 15),
        release_level="final",
        implementation="cpython",
        architecture="64bit",
        machine="AMD64",
        prefix=path.parent,
        base_prefix=path.parent,
        is_venv=is_venv,
        pip_available=is_venv,
        venv_available=True,
        ensurepip_available=True,
    )


class FakeResolver:
    def __init__(self, *, broken: bool = False) -> None:
        self.broken = broken

    def probe(self, candidate: RuntimeCandidate) -> RuntimeProbe:
        if self.broken:
            return RuntimeProbe(
                candidate,
                False,
                candidate.executable,
                error="base interpreter was removed",
            )
        return runtime_probe(candidate.executable, is_venv=True)


class FakeRunner:
    def __init__(self, *, fail_create: bool = False) -> None:
        self.fail_create = fail_create
        self.commands: list[tuple[str, ...]] = []

    def run(self, spec, **_kwargs):
        self.commands.append(spec.argv)
        if len(spec.argv) >= 4 and spec.argv[2:4] == ("-m", "venv"):
            if self.fail_create:
                return command_result(spec, success=False, stderr="creation failed")
            environment = Path(spec.argv[4])
            python = VenvManager.python_path(environment)
            python.parent.mkdir(parents=True, exist_ok=True)
            python.touch()
            (environment / "pyvenv.cfg").write_text(
                f"home = {Path(spec.argv[0]).parent}\n", encoding="utf-8"
            )
        return command_result(spec)


def prepare_environment(root: Path, base: Path) -> None:
    python = VenvManager.python_path(root)
    python.parent.mkdir(parents=True)
    python.touch()
    (root / "pyvenv.cfg").write_text(f"home = {base}\n", encoding="utf-8")


class VenvManagerTests(unittest.TestCase):
    def test_inspect_reports_missing_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "venv"
            manager = VenvManager(FakeRunner(), FakeResolver())  # type: ignore[arg-type]

            health = manager.inspect(root)

            self.assertEqual(health.state, EnvironmentState.MISSING)

    def test_inspect_detects_executable_with_removed_base_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "venv"
            base = Path(temporary) / "base-python"
            prepare_environment(root, base)
            manager = VenvManager(  # type: ignore[arg-type]
                FakeRunner(), FakeResolver(broken=True), trusted_bases=(base,)
            )

            health = manager.inspect(root)

            self.assertEqual(health.state, EnvironmentState.EXECUTABLE_BROKEN)
            self.assertIn("removed", health.detail)

    def test_inspect_requires_working_pip(self) -> None:
        class PipFailureRunner(FakeRunner):
            def run(self, spec, **kwargs):
                if "pip" in spec.argv:
                    return command_result(spec, success=False, stderr="No module named pip")
                return super().run(spec, **kwargs)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "venv"
            base = Path(temporary) / "base-python"
            prepare_environment(root, base)
            manager = VenvManager(  # type: ignore[arg-type]
                PipFailureRunner(), FakeResolver(), trusted_bases=(base,)
            )

            health = manager.inspect(root)

            self.assertEqual(health.state, EnvironmentState.PIP_BROKEN)

    def test_untrusted_base_is_not_executed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "venv"
            foreign_base = Path(temporary) / "other-project-python"
            prepare_environment(root, foreign_base)
            runner = FakeRunner()
            manager = VenvManager(runner, FakeResolver())  # type: ignore[arg-type]

            health = manager.inspect(root)

            self.assertEqual(health.state, EnvironmentState.UNTRUSTED_BASE)
            self.assertEqual(runner.commands, [])

    def test_successful_recreate_commits_and_removes_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "venv"
            root.mkdir()
            (root / "old-marker.txt").write_text("old", encoding="utf-8")
            runner = FakeRunner()
            manager = VenvManager(runner, FakeResolver())  # type: ignore[arg-type]
            runtime = runtime_probe(Path("C:/Python313/python.exe"), is_venv=False)

            result = manager.recreate(root, runtime)

            self.assertTrue(result.succeeded, result.detail)
            self.assertEqual(result.stage, RepairStage.COMMIT)
            self.assertTrue(VenvManager.python_path(root).exists())
            self.assertFalse((root / "old-marker.txt").exists())
            self.assertFalse(any(root.parent.glob("venv.recovery-*")))

    def test_creation_failure_restores_previous_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "venv"
            root.mkdir()
            (root / "old-marker.txt").write_text("old", encoding="utf-8")
            manager = VenvManager(
                FakeRunner(fail_create=True), FakeResolver()  # type: ignore[arg-type]
            )
            runtime = runtime_probe(Path("C:/Python313/python.exe"), is_venv=False)

            result = manager.recreate(root, runtime)

            self.assertFalse(result.succeeded)
            self.assertTrue(result.rolled_back)
            self.assertTrue((root / "old-marker.txt").exists())

    def test_validation_failure_restores_previous_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "venv"
            root.mkdir()
            (root / "old-marker.txt").write_text("old", encoding="utf-8")
            manager = VenvManager(FakeRunner(), FakeResolver())  # type: ignore[arg-type]
            runtime = runtime_probe(Path("C:/Python313/python.exe"), is_venv=False)

            result = manager.recreate(
                root,
                runtime,
                validate=lambda _python: (False, "requirements are incomplete"),
            )

            self.assertFalse(result.succeeded)
            self.assertEqual(result.stage, RepairStage.VALIDATE)
            self.assertTrue(result.rolled_back)
            self.assertTrue((root / "old-marker.txt").exists())

    def test_recreate_rejects_an_installation_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            installation = Path(temporary) / "ComfyUI"
            installation.mkdir()
            marker = installation / "main.py"
            marker.write_text("safe", encoding="utf-8")
            manager = VenvManager(FakeRunner(), FakeResolver())  # type: ignore[arg-type]
            runtime = runtime_probe(Path("C:/Python313/python.exe"), is_venv=False)

            with self.assertRaises(ValueError):
                manager.recreate(installation, runtime)

            self.assertEqual(marker.read_text(encoding="utf-8"), "safe")

    def test_finalize_recovery_removes_only_matching_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "venv"
            backup = Path(temporary) / "venv.recovery-20260830"
            unrelated = Path(temporary) / "models"
            backup.mkdir()
            unrelated.mkdir()
            manager = VenvManager(FakeRunner(), FakeResolver())  # type: ignore[arg-type]

            succeeded, _ = manager.finalize_recovery(root, backup)
            refused, _ = manager.finalize_recovery(root, unrelated)

            self.assertTrue(succeeded)
            self.assertFalse(backup.exists())
            self.assertFalse(refused)
            self.assertTrue(unrelated.exists())


if __name__ == "__main__":
    unittest.main()
