from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from smart_launcher.application.installation_pipeline import InstallationPipeline
from smart_launcher.domain.environments import (
    EnvironmentHealth,
    EnvironmentState,
    RepairResult,
    RepairStage,
)
from smart_launcher.domain.installation import (
    InstallationRequest,
    InstallationStage,
)
from smart_launcher.domain.runtimes import RuntimeCandidate, RuntimeProbe, RuntimeSource
from smart_launcher.domain.updates import UpdateChannel


def git(cwd: Path, *arguments: str) -> str:
    executable = shutil.which("git")
    if executable is None:
        raise unittest.SkipTest("Git is unavailable")
    result = subprocess.run(
        [executable, *arguments],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def runtime(root: Path) -> RuntimeProbe:
    executable = root / "python.exe"
    return RuntimeProbe(
        RuntimeCandidate(executable, RuntimeSource.CONFIGURED),
        True,
        executable,
        version="3.13.15",
        version_info=(3, 13, 15),
        release_level="final",
        implementation="cpython",
        architecture="64bit",
        prefix=root,
        base_prefix=root,
        venv_available=True,
        ensurepip_available=True,
    )


class _EnvironmentService:
    def __init__(self, succeeds=True) -> None:
        self.succeeds = succeeds
        self.calls = 0
        self.manifests = ()

    def rebuild(self, environment, _runtime, manifests, **_kwargs):
        self.calls += 1
        self.manifests = tuple(manifests)
        health = EnvironmentHealth(
            environment,
            environment / "Scripts" / "python.exe",
            EnvironmentState.HEALTHY,
            "ok",
        )
        if self.succeeds:
            environment.mkdir(parents=True)
            (environment / "installed.txt").write_text("ok", encoding="utf-8")
        return RepairResult(
            self.succeeds,
            RepairStage.COMMIT if self.succeeds else RepairStage.PROVISION,
            health,
            health if self.succeeds else None,
            None,
            "ok" if self.succeeds else "dependency installation failed",
            rolled_back=not self.succeeds,
        )


class InstallationPipelineTests(unittest.TestCase):
    def test_packaged_default_is_exe_directory_not_working_directory(self):
        from unittest.mock import patch
        from smart_launcher.bootstrap import _default_install_target
        executable = self.root / "portable" / "SmartLauncher2.exe"
        with patch("sys.frozen", True, create=True), patch("sys.executable", str(executable)):
            self.assertEqual(_default_install_target(), executable.parent.resolve())
        with patch("sys.frozen", False, create=True):
            self.assertIsNone(_default_install_target())

    def test_install_alongside_running_launcher_keeps_exe_and_git_clean(self):
        from smart_launcher.comfy.repository import ComfyRepository
        target = self.root / "ComfyUI"
        target.mkdir()
        executable = target / "Renamed Launcher.exe"
        executable.write_bytes(b"launcher")
        pipeline = InstallationPipeline(_EnvironmentService(), launcher_executable=executable)
        result = pipeline.execute(InstallationRequest(target, str(self.source)), runtime(self.root / "Python313"))
        self.assertTrue(result.succeeded, result.detail)
        self.assertEqual(executable.read_bytes(), b"launcher")
        self.assertTrue((target / "main.py").is_file())
        self.assertNotIn(executable.name, ComfyRepository(target).inspect().dirty_paths)

    def test_failed_install_alongside_launcher_keeps_original_and_new_user_file(self):
        target = self.root / "ComfyUI"
        target.mkdir()
        executable = target / "SmartLauncher2.exe"
        executable.write_bytes(b"launcher")
        class FailingEnvironment(_EnvironmentService):
            def rebuild(self, *args, **kwargs):
                (target / "user-note.txt").write_text("keep")
                return super().rebuild(*args, **kwargs)
        pipeline = InstallationPipeline(FailingEnvironment(False), launcher_executable=executable)
        result = pipeline.execute(InstallationRequest(target, str(self.source)), runtime(self.root / "Python313"))
        self.assertFalse(result.succeeded)
        self.assertTrue(result.rolled_back, result.detail)
        self.assertEqual(executable.read_bytes(), b"launcher")
        self.assertEqual({p.name for p in target.iterdir()}, {executable.name, "user-note.txt"})

    def test_extra_file_beside_launcher_still_blocks_installation(self):
        target = self.root / "ComfyUI"
        target.mkdir()
        executable = target / "SmartLauncher2.exe"
        executable.write_bytes(b"launcher")
        (target / "important.txt").write_text("keep")
        pipeline = InstallationPipeline(_EnvironmentService(), launcher_executable=executable)
        result = pipeline.execute(InstallationRequest(target, str(self.source)), runtime(self.root / "Python313"))
        self.assertEqual(result.stage, InstallationStage.PREFLIGHT)
        self.assertFalse(result.succeeded)
        self.assertEqual((target / "important.txt").read_text(), "keep")

    def test_partial_placement_failure_preserves_launcher(self):
        from unittest.mock import patch
        target = self.root / "ComfyUI"
        target.mkdir()
        executable = target / "SmartLauncher2.exe"
        executable.write_bytes(b"launcher")
        rename = Path.rename
        def fail(path, destination):
            if path.name == "requirements.txt":
                raise OSError("simulated placement failure")
            return rename(path, destination)
        pipeline = InstallationPipeline(_EnvironmentService(), launcher_executable=executable)
        with patch.object(Path, "rename", fail):
            result = pipeline.execute(InstallationRequest(target, str(self.source)), runtime(self.root / "Python313"))
        self.assertFalse(result.succeeded)
        self.assertEqual(list(target.iterdir()), [executable])
        self.assertEqual(executable.read_bytes(), b"launcher")

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        git(self.source, "init", "-b", "master")
        git(self.source, "config", "user.name", "Smart Launcher Tests")
        git(self.source, "config", "user.email", "tests@example.invalid")
        (self.source / "main.py").write_text("print('v1')\n", encoding="utf-8")
        (self.source / "requirements.txt").write_text("core==1\n", encoding="utf-8")
        (self.source / "manager_requirements.txt").write_text(
            "manager==1\n", encoding="utf-8"
        )
        git(self.source, "add", ".")
        git(self.source, "commit", "-m", "stable")
        git(self.source, "tag", "v1.0.0")
        self.stable_commit = git(self.source, "rev-parse", "HEAD")
        (self.source / "main.py").write_text("print('development')\n", encoding="utf-8")
        git(self.source, "add", "main.py")
        git(self.source, "commit", "-m", "development")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_stable_install_places_checkout_then_builds_final_venv(self) -> None:
        target = self.root / "ComfyUI"
        environments = _EnvironmentService()
        pipeline = InstallationPipeline(environments)  # type: ignore[arg-type]

        result = pipeline.execute(
            InstallationRequest(target, str(self.source), UpdateChannel.STABLE),
            runtime(self.root / "Python313"),
        )

        self.assertTrue(result.succeeded, result.detail)
        self.assertEqual(result.stage, InstallationStage.COMMIT)
        self.assertEqual(git(target, "rev-parse", "HEAD"), self.stable_commit)
        self.assertTrue((target / "venv" / "installed.txt").is_file())
        self.assertFalse((target / InstallationPipeline.MARKER).exists())
        self.assertEqual(
            tuple(path.name for path in environments.manifests),
            ("requirements.txt", "manager_requirements.txt"),
        )

    def test_nonempty_target_is_refused_without_modification(self) -> None:
        target = self.root / "ComfyUI"
        target.mkdir()
        marker = target / "user-file.txt"
        marker.write_text("keep", encoding="utf-8")
        environments = _EnvironmentService()
        pipeline = InstallationPipeline(environments)  # type: ignore[arg-type]

        result = pipeline.execute(
            InstallationRequest(target, str(self.source)),
            runtime(self.root / "Python313"),
        )

        self.assertFalse(result.succeeded)
        self.assertEqual(result.stage, InstallationStage.PREFLIGHT)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
        self.assertEqual(environments.calls, 0)

    def test_environment_failure_removes_owned_install_and_restores_empty_folder(self) -> None:
        target = self.root / "ComfyUI"
        target.mkdir()
        environments = _EnvironmentService(False)
        pipeline = InstallationPipeline(environments)  # type: ignore[arg-type]

        result = pipeline.execute(
            InstallationRequest(target, str(self.source), UpdateChannel.DEVELOPMENT),
            runtime(self.root / "Python313"),
        )

        self.assertFalse(result.succeeded)
        self.assertEqual(result.stage, InstallationStage.ROLLBACK)
        self.assertTrue(result.rolled_back)
        self.assertTrue(target.is_dir())
        self.assertEqual(tuple(target.iterdir()), ())
        self.assertFalse(any(self.root.glob(".ComfyUI.installing-*")))

    def test_repository_without_comfy_contract_is_cleaned(self) -> None:
        invalid = self.root / "invalid"
        invalid.mkdir()
        git(invalid, "init", "-b", "master")
        git(invalid, "config", "user.name", "Smart Launcher Tests")
        git(invalid, "config", "user.email", "tests@example.invalid")
        (invalid / "README.md").write_text("not comfy", encoding="utf-8")
        git(invalid, "add", ".")
        git(invalid, "commit", "-m", "invalid")
        git(invalid, "tag", "v1.0.0")
        target = self.root / "ComfyUI"
        pipeline = InstallationPipeline(_EnvironmentService())  # type: ignore[arg-type]

        result = pipeline.execute(
            InstallationRequest(target, str(invalid)),
            runtime(self.root / "Python313"),
        )

        self.assertFalse(result.succeeded)
        self.assertFalse(target.exists())
        self.assertFalse(any(self.root.glob(".ComfyUI.installing-*")))


if __name__ == "__main__":
    unittest.main()
