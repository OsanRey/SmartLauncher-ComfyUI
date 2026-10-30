from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smart_launcher.comfy.repository import ComfyRepository, RepositoryError
from smart_launcher.domain.updates import UpdateChannel


def git(cwd: Path, *args: str) -> str:
    executable = shutil.which("git")
    if executable is None:
        raise unittest.SkipTest("Git is unavailable")
    result = subprocess.run(
        [executable, *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


class RepositoryTests(unittest.TestCase):
    def test_running_untracked_launcher_does_not_block_existing_checkout(self):
        executable = self.checkout / "SmartLauncher2.exe"
        executable.write_bytes(b"launcher")
        repository = ComfyRepository(self.checkout)
        with patch("sys.frozen", True, create=True), patch("sys.executable", str(executable)):
            self.assertTrue(repository.inspect().clean)
            (self.checkout / "other.exe").write_bytes(b"other")
            self.assertIn("other.exe", repository.inspect().dirty_paths)
        self.assertIn(executable.name, repository.inspect().dirty_paths)

    def test_tracked_launcher_modification_is_not_ignored(self):
        executable = self.checkout / "SmartLauncher2.exe"
        executable.write_bytes(b"original")
        git(self.checkout, "config", "user.name", "Test")
        git(self.checkout, "config", "user.email", "test@example.invalid")
        git(self.checkout, "add", executable.name)
        git(self.checkout, "commit", "-m", "tracked exe")
        executable.write_bytes(b"changed")
        with patch("sys.frozen", True, create=True), patch("sys.executable", str(executable)):
            self.assertIn(executable.name, ComfyRepository(self.checkout).inspect().dirty_paths)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.remote = root / "remote.git"
        self.seed = root / "seed"
        self.checkout = root / "checkout"
        self.remote.mkdir()
        git(self.remote, "init", "--bare")
        self.seed.mkdir()
        git(self.seed, "init", "-b", "master")
        git(self.seed, "config", "user.name", "Smart Launcher Tests")
        git(self.seed, "config", "user.email", "tests@example.invalid")
        (self.seed / "requirements.txt").write_text("core==1\n", encoding="utf-8")
        (self.seed / "manager_requirements.txt").write_text("manager==1\n", encoding="utf-8")
        (self.seed / "models").mkdir()
        (self.seed / "models" / "put_models_here").write_text("", encoding="utf-8")
        (self.seed / "output").mkdir()
        (self.seed / "output" / "_output_images_will_be_put_here").write_text(
            "", encoding="utf-8"
        )
        git(self.seed, "add", ".")
        git(self.seed, "commit", "-m", "v1")
        git(self.seed, "tag", "v1.0.0")
        git(self.seed, "remote", "add", "origin", str(self.remote))
        git(self.seed, "push", "-u", "origin", "master", "--tags")
        git(root, "clone", str(self.remote), str(self.checkout))

        (self.seed / "requirements.txt").write_text("core==2\n", encoding="utf-8")
        git(self.seed, "add", "requirements.txt")
        git(self.seed, "commit", "-m", "v2")
        git(self.seed, "tag", "v1.1.0")
        git(self.seed, "push", "origin", "master", "--tags")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_stable_plan_detects_changed_core_manifest(self) -> None:
        repository = ComfyRepository(self.checkout)

        plan = repository.prepare(UpdateChannel.STABLE)

        self.assertTrue(plan.update_available)
        self.assertEqual(plan.target_ref, "refs/tags/v1.1.0")
        self.assertEqual(plan.changed_manifests, ("requirements.txt",))
        self.assertEqual(plan.current_label, "v1.0.0")
        self.assertIn("v1.1.0", plan.version_summary)
        self.assertTrue(any(line.endswith(" v2") for line in plan.changes))
        self.assertIn("-core==1", plan.requirements_diff)
        self.assertIn("+core==2", plan.requirements_diff)
        self.assertEqual(plan.compare_url, "")

    def test_github_compare_url_uses_actual_remote(self) -> None:
        repository = ComfyRepository(self.checkout)
        repository.prepare(UpdateChannel.STABLE)
        git(self.checkout, "remote", "set-url", "origin", "https://github.com/example/ComfyUI.git")
        plan = repository.prepare(UpdateChannel.STABLE, fetch=False)
        self.assertEqual(
            plan.compare_url,
            f"https://github.com/example/ComfyUI/compare/{plan.current_commit}...{plan.target_commit}",
        )

    def test_dirty_repository_is_not_updated(self) -> None:
        (self.checkout / "local.txt").write_text("mine", encoding="utf-8")
        repository = ComfyRepository(self.checkout)

        with self.assertRaises(RepositoryError):
            repository.prepare(UpdateChannel.DEVELOPMENT)

    def test_deleted_output_placeholder_does_not_block_update(self) -> None:
        placeholder = self.checkout / "output" / "_output_images_will_be_put_here"
        placeholder.unlink()
        repository = ComfyRepository(self.checkout)

        state = repository.inspect()
        plan = repository.prepare(UpdateChannel.DEVELOPMENT)
        result = repository.apply(plan, validate=lambda _plan: (True, "ok"))

        self.assertTrue(state.clean)
        self.assertTrue(result.succeeded, result.detail)
        self.assertFalse(placeholder.exists())

    def test_linked_overlay_is_preserved_while_core_switches(self) -> None:
        overlay = self.checkout / "models"
        shutil.rmtree(overlay)
        overlay.mkdir()
        user_model = overlay / "user-model.safetensors"
        user_model.write_bytes(b"model")
        repository = ComfyRepository(self.checkout)

        with patch.object(
            repository,
            "_active_overlays",
            return_value=(("models", overlay),),
        ):
            plan = repository.prepare(UpdateChannel.DEVELOPMENT)
            result = repository.apply(plan, validate=lambda _plan: (True, "ok"))

        self.assertTrue(result.succeeded, result.detail)
        self.assertEqual(user_model.read_bytes(), b"model")
        self.assertFalse((overlay / "put_models_here").exists())
        self.assertEqual(git(self.checkout, "rev-parse", "HEAD"), plan.target_commit)

    def test_validation_failure_rolls_back_previous_commit(self) -> None:
        repository = ComfyRepository(self.checkout)
        plan = repository.prepare(UpdateChannel.DEVELOPMENT)

        result = repository.apply(plan, validate=lambda _plan: (False, "broken dependencies"))

        self.assertFalse(result.succeeded)
        self.assertTrue(result.rolled_back)
        self.assertEqual(git(self.checkout, "rev-parse", "HEAD"), plan.current_commit)

    def test_successful_update_switches_to_planned_commit(self) -> None:
        repository = ComfyRepository(self.checkout)
        plan = repository.prepare(UpdateChannel.DEVELOPMENT)

        result = repository.apply(plan, validate=lambda _plan: (True, "ok"))

        self.assertTrue(result.succeeded)
        self.assertEqual(git(self.checkout, "rev-parse", "HEAD"), plan.target_commit)


if __name__ == "__main__":
    unittest.main()
