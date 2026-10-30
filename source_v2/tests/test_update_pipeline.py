from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from smart_launcher.application.update_pipeline import CoreUpdatePipeline
from smart_launcher.domain.environments import (
    EnvironmentHealth,
    EnvironmentState,
    RepairResult,
    RepairStage,
)
from smart_launcher.domain.maintenance import DependencySyncResult, MaintenanceStage
from smart_launcher.domain.runtimes import RuntimeCandidate, RuntimeProbe, RuntimeSource
from smart_launcher.domain.updates import UpdateChannel, UpdatePlan, UpdateResult


def runtime(path: Path) -> RuntimeProbe:
    return RuntimeProbe(
        RuntimeCandidate(path, RuntimeSource.CONFIGURED),
        True,
        path,
        version="3.13.15",
        version_info=(3, 13, 15),
        release_level="final",
        implementation="cpython",
        architecture="64bit",
        prefix=path.parent,
        base_prefix=path.parent,
        venv_available=True,
        ensurepip_available=True,
    )


class _Repository:
    MANIFESTS = ("requirements.txt", "manager_requirements.txt")

    def __init__(self, plan: UpdatePlan) -> None:
        self.plan = plan
        self.root = plan.root
        self.prepare_calls = 0

    def prepare(self, _channel, *, fetch=True):
        self.prepare_calls += 1
        return self.plan

    def apply(self, plan, *, validate=None):
        valid, detail = validate(plan) if validate else (True, "ok")
        return UpdateResult(
            valid,
            plan,
            plan.target_commit if valid else plan.current_commit,
            detail,
            rolled_back=not valid,
        )


class _Environments:
    def __init__(self, *, repair_succeeds: bool = True, healthy: bool = True) -> None:
        self.repair_succeeds = repair_succeeds
        self.healthy = healthy
        self.recreate_calls = 0
        self.finalize_calls = 0
        self.trusted_base = None

    @staticmethod
    def python_path(root: Path) -> Path:
        return root / "Scripts" / "python.exe"

    def recreate(self, root, _runtime, *, provision, validate, keep_backup, **_kwargs):
        self.recreate_calls += 1
        python = self.python_path(root)
        try:
            provision(python)
            valid, detail = validate(python)
        except Exception as exc:
            valid, detail = False, str(exc)
        succeeded = self.repair_succeeds and valid
        health = EnvironmentHealth(root, python, EnvironmentState.HEALTHY, "ok")
        return RepairResult(
            succeeded,
            RepairStage.COMMIT if succeeded else RepairStage.VALIDATE,
            health,
            health if succeeded else None,
            root.with_name("venv.recovery-test") if keep_backup and succeeded else None,
            detail,
            rolled_back=not succeeded,
        )

    def inspect(self, root, *, trusted_base=None):
        self.trusted_base = trusted_base
        state = EnvironmentState.HEALTHY if self.healthy else EnvironmentState.UNTRUSTED_BASE
        return EnvironmentHealth(
            root,
            self.python_path(root),
            state,
            "ok" if self.healthy else "foreign base",
        )

    def finalize_recovery(self, _root, _backup):
        self.finalize_calls += 1
        return True, "removed"


class _Dependencies:
    def __init__(self, succeeds: bool = True) -> None:
        self.succeeds = succeeds
        self.calls = 0
        self.last_manifests = ()
        self.upgrade_values = []
        self.results = []

    def sync(self, _python, manifests, **_kwargs):
        self.calls += 1
        self.last_manifests = tuple(manifests)
        self.upgrade_values.append(_kwargs.get("upgrade", True))
        succeeds = self.results.pop(0) if self.results else self.succeeds
        return DependencySyncResult(
            succeeds,
            tuple(manifests) if succeeds else (),
            "ok" if succeeds else "pip failed",
        )


class _Requirements:
    def __init__(self, healthy: bool = True) -> None:
        self.healthy = healthy

    def validate(self, _python, _manifest):
        return SimpleNamespace(healthy=self.healthy)


class _Torch:
    def __init__(self, compatible: bool = True) -> None:
        self.compatible = compatible

    def validate(self, _python, _expectation):
        return SimpleNamespace(compatible=self.compatible, errors=("torch mismatch",))


class _CoreSmoke:
    def __init__(self, compatible: bool = True) -> None:
        self.compatible = compatible
        self.calls = 0

    def validate(self, _python, _root, **_kwargs):
        self.calls += 1
        return self.compatible, "core ok" if self.compatible else "core failed"


class _CoreSmokeSequence:
    def __init__(self, *results: bool) -> None:
        self.results = list(results)
        self.calls = 0

    def validate(self, _python, _root, **_kwargs):
        self.calls += 1
        compatible = self.results.pop(0)
        return compatible, "core ok" if compatible else "core failed"


class UpdatePipelineTests(unittest.TestCase):
    def _fixture(self, root: Path, *, update: bool = True, requirements_healthy=True):
        (root / "requirements.txt").write_text("core==2\n", encoding="utf-8")
        (root / "manager_requirements.txt").write_text("manager==2\n", encoding="utf-8")
        plan = UpdatePlan(
            root,
            UpdateChannel.STABLE,
            "old",
            "new" if update else "old",
            "refs/tags/v2",
            ("requirements.txt",) if update else (),
        )
        repository = _Repository(plan)
        environments = _Environments()
        dependencies = _Dependencies()
        pipeline = CoreUpdatePipeline(
            repository,  # type: ignore[arg-type]
            environments,  # type: ignore[arg-type]
            dependencies,  # type: ignore[arg-type]
            _Requirements(requirements_healthy),  # type: ignore[arg-type]
            _Torch(),  # type: ignore[arg-type]
            server_is_stopped=lambda: True,
            core_smoke=_CoreSmoke(),  # type: ignore[arg-type]
        )
        self.addCleanup(pipeline.update_marker.complete)
        return pipeline, repository, environments, dependencies

    def test_pending_update_is_detected_without_fetch(self):
        with tempfile.TemporaryDirectory() as directory:
            pipeline, repository, _, _ = self._fixture(Path(directory), update=False)
            pipeline.update_marker.begin(repository.plan)
            result = pipeline.check_update(UpdateChannel.STABLE)
            self.assertFalse(result.succeeded)
            self.assertIn("не завершено", result.detail)
            self.assertEqual(repository.prepare_calls, 0)

    def test_pending_current_revision_is_validated_before_clearing_marker(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pipeline, repository, environments, dependencies = self._fixture(root, update=False)
            pipeline.update_marker.begin(repository.plan)
            pipeline.integrity = Mock()
            pipeline.integrity.verify.return_value = SimpleNamespace(succeeded=True)
            result = pipeline.execute(UpdateChannel.STABLE, root / "venv", runtime(root / "py"))
            self.assertTrue(result.succeeded, result.detail)
            self.assertFalse(pipeline.update_marker.pending)
            self.assertEqual(dependencies.calls, 1)
            self.assertEqual(len(dependencies.last_manifests), 2)
            pipeline.integrity.verify.assert_called_once()
            self.assertEqual(pipeline.core_smoke.calls, 1)
            self.assertEqual(environments.recreate_calls, 0)

    def test_pending_integrity_failure_keeps_marker(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pipeline, repository, _, _ = self._fixture(root, update=False)
            pipeline.update_marker.begin(repository.plan)
            pipeline.integrity = Mock()
            pipeline.integrity.verify.return_value = SimpleNamespace(
                succeeded=False, exception=None, stdout="damaged package", stderr="",
            )
            result = pipeline.execute(UpdateChannel.STABLE, root / "venv", runtime(root / "py"))
            self.assertFalse(result.succeeded)
            self.assertTrue(pipeline.update_marker.pending)
            self.assertEqual(pipeline.core_smoke.calls, 0)

    def test_running_server_blocks_before_git_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pipeline, repository, _, _ = self._fixture(root)
            pipeline.server_is_stopped = lambda: False

            result = pipeline.execute(UpdateChannel.STABLE, root / "venv", runtime(root / "py"))

            self.assertFalse(result.succeeded)
            self.assertEqual(result.stage, MaintenanceStage.PREFLIGHT)
            self.assertEqual(repository.prepare_calls, 0)

    def test_update_check_only_builds_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pipeline, repository, environments, dependencies = self._fixture(root)

            result = pipeline.check_update(UpdateChannel.STABLE)

            self.assertTrue(result.succeeded)
            self.assertTrue(result.plan.update_available)
            self.assertEqual(repository.prepare_calls, 1)
            self.assertEqual(environments.recreate_calls, 0)
            self.assertEqual(dependencies.calls, 0)

    def test_changed_manifest_updates_existing_venv_then_commits_core(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pipeline, _, environments, dependencies = self._fixture(root)

            result = pipeline.execute(UpdateChannel.STABLE, root / "venv", runtime(root / "py"))

            self.assertTrue(result.succeeded, result.detail)
            self.assertEqual(result.stage, MaintenanceStage.COMMIT)
            self.assertEqual(environments.recreate_calls, 0)
            self.assertEqual(dependencies.calls, 1)
            self.assertEqual(
                tuple(path.name for path in dependencies.last_manifests),
                ("requirements.txt",),
            )
            self.assertEqual(dependencies.upgrade_values, [False])
            self.assertEqual(environments.finalize_calls, 0)

    def test_failed_dependency_sync_rolls_back_core_and_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pipeline, _, environments, dependencies = self._fixture(root)
            dependencies.results = [False, True]

            result = pipeline.execute(UpdateChannel.STABLE, root / "venv", runtime(root / "py"))

            self.assertFalse(result.succeeded)
            self.assertTrue(result.core_rolled_back)
            self.assertTrue(pipeline.update_marker.pending)
            self.assertTrue(result.environment_rolled_back)
            self.assertEqual(environments.recreate_calls, 0)
            self.assertEqual(dependencies.calls, 2)
            self.assertEqual(dependencies.upgrade_values, [False, False])
            self.assertEqual(environments.finalize_calls, 0)

    def test_current_core_does_not_touch_stale_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pipeline, _, environments, dependencies = self._fixture(
                root, update=False, requirements_healthy=False
            )
            result = pipeline.execute(UpdateChannel.STABLE, root / "venv", runtime(root / "py"))

            self.assertTrue(result.succeeded, result.detail)
            self.assertEqual(environments.recreate_calls, 0)
            self.assertEqual(dependencies.calls, 0)

    def test_untrusted_existing_venv_is_not_validated_before_repair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pipeline, _, environments, dependencies = self._fixture(root, update=False)
            environments.healthy = False

            class FailsIfCalledBeforeRepair:
                calls = 0

                def validate(self, _python, _manifest):
                    self.calls += 1
                    return SimpleNamespace(healthy=True)

            requirements = FailsIfCalledBeforeRepair()
            pipeline.requirements = requirements  # type: ignore[assignment]
            pipeline.environment_service.requirements = requirements

            result = pipeline.repair_environment(
                root / "venv",
                runtime(root / "trusted-python"),
            )

            self.assertTrue(result.succeeded, result.detail)
            self.assertEqual(environments.recreate_calls, 1)
            self.assertEqual(dependencies.calls, 1)
            self.assertGreater(requirements.calls, 0)
            self.assertEqual(environments.trusted_base, root)

    def test_update_with_unchanged_manifest_rolls_back_without_repair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pipeline, _, environments, dependencies = self._fixture(root)
            pipeline.repository.plan = UpdatePlan(
                root,
                UpdateChannel.STABLE,
                "old",
                "new",
                "refs/tags/v2",
                (),
            )
            smoke = _CoreSmokeSequence(False)
            pipeline.core_smoke = smoke  # type: ignore[assignment]

            result = pipeline.execute(UpdateChannel.STABLE, root / "venv", runtime(root / "py"))

            self.assertFalse(result.succeeded)
            self.assertEqual(environments.recreate_calls, 0)
            self.assertEqual(dependencies.calls, 0)
            self.assertEqual(smoke.calls, 1)

    def test_explicit_environment_repair_rebuilds_stale_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pipeline, _, environments, dependencies = self._fixture(
                root, update=False, requirements_healthy=False
            )
            class BecomesHealthyAfterRepair:
                calls = 0

                def validate(self, _python, _manifest):
                    self.calls += 1
                    return SimpleNamespace(healthy=self.calls > 1)

            requirements = BecomesHealthyAfterRepair()
            pipeline.requirements = requirements  # type: ignore[assignment]
            pipeline.environment_service.requirements = requirements

            result = pipeline.repair_environment(root / "venv", runtime(root / "py"))

            self.assertTrue(result.succeeded, result.detail)
            self.assertEqual(environments.recreate_calls, 1)
            self.assertEqual(dependencies.calls, 1)

    def test_core_update_does_not_reinstall_custom_node_requirements(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            custom = root / "custom_nodes" / "sample-node"
            custom.mkdir(parents=True)
            (custom / "requirements.txt").write_text("node-package==1\n", encoding="utf-8")
            pipeline, _, environments, dependencies = self._fixture(root)

            result = pipeline.execute(UpdateChannel.STABLE, root / "venv", runtime(root / "py"))

            self.assertTrue(result.succeeded, result.detail)
            self.assertEqual(
                tuple(path.relative_to(root).as_posix() for path in dependencies.last_manifests),
                ("requirements.txt",),
            )
            self.assertEqual(environments.recreate_calls, 0)


if __name__ == "__main__":
    unittest.main()
