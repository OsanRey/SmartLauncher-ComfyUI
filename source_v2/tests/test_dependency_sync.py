from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from smart_launcher.domain.commands import CommandFailure, CommandResult
from smart_launcher.runtime.dependency_sync import DependencySynchronizer


class _Runner:
    def __init__(self, fail_source: str | None = None) -> None:
        self.fail_source = fail_source
        self.specs = []

    def run(self, spec, **_kwargs):
        self.specs.append(spec)
        now = datetime.now(UTC)
        failed = spec.source == self.fail_source
        return CommandResult(
            spec,
            CommandFailure.NONZERO_EXIT if failed else CommandFailure.NONE,
            1 if failed else 0,
            now,
            now,
            stderr="simulated pip failure" if failed else "",
        )


class DependencySynchronizerTests(unittest.TestCase):
    def test_integrity_failure_stops_before_pip_check(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "requirements.txt"
            manifest.write_text("package==1\n")
            runner = _Runner("package-integrity")
            result = DependencySynchronizer(runner).sync(Path("python.exe"), (manifest,))
            self.assertFalse(result.succeeded)
            self.assertEqual([s.source for s in runner.specs], ["pip-requirements", "package-integrity"])

    def test_preserves_manifest_and_index_as_single_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "requirements with spaces.txt"
            manifest.write_text("package==1\n", encoding="utf-8")
            runner = _Runner()
            synchronizer = DependencySynchronizer(runner)  # type: ignore[arg-type]

            result = synchronizer.sync(
                Path(directory) / "venv" / "Scripts" / "python.exe",
                (manifest,),
                torch_packages=("torch", "torchvision"),
                torch_index_url="https://download.pytorch.org/whl/cu130",
            )

            self.assertTrue(result.succeeded)
            self.assertEqual(len(runner.specs), 5)
            self.assertIn(str(manifest.resolve()), runner.specs[2].argv)
            self.assertEqual(runner.specs[1].source, "package-integrity")
            self.assertEqual(runner.specs[3].source, "package-integrity")
            self.assertIn("https://download.pytorch.org/whl/cu130", runner.specs[0].argv)
            self.assertEqual(runner.specs[-1].argv[-1], "check")

    def test_missing_manifest_fails_before_starting_pip(self) -> None:
        runner = _Runner()
        synchronizer = DependencySynchronizer(runner)  # type: ignore[arg-type]

        result = synchronizer.sync(Path("python.exe"), (Path("missing.txt"),))

        self.assertFalse(result.succeeded)
        self.assertEqual(runner.specs, [])

    def test_incremental_sync_does_not_upgrade_or_install_torch_separately(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "requirements.txt"
            manifest.write_text("small-package==2\n", encoding="utf-8")
            runner = _Runner()
            synchronizer = DependencySynchronizer(runner)  # type: ignore[arg-type]

            result = synchronizer.sync(
                Path(directory) / "venv" / "Scripts" / "python.exe",
                (manifest,),
                upgrade=False,
            )

            self.assertTrue(result.succeeded)
            self.assertEqual(len(runner.specs), 3)
            install = runner.specs[0].argv
            self.assertIn("install", install)
            self.assertNotIn("--upgrade", install)
            self.assertNotIn("torch", install)
            self.assertEqual(runner.specs[-1].argv[-1], "check")

    def test_stops_after_failed_requirements_install(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "requirements.txt"
            manifest.write_text("package==1\n", encoding="utf-8")
            runner = _Runner("pip-requirements")
            synchronizer = DependencySynchronizer(runner)  # type: ignore[arg-type]

            result = synchronizer.sync(Path("python.exe"), (manifest,))

            self.assertFalse(result.succeeded)
            self.assertEqual(len(runner.specs), 1)
            self.assertIn("simulated pip failure", result.detail)


if __name__ == "__main__":
    unittest.main()
