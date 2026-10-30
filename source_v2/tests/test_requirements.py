from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from smart_launcher.domain.commands import CommandFailure, CommandResult
from smart_launcher.domain.dependencies import RequirementState
from smart_launcher.runtime.requirements import RequirementsValidator


class SnapshotRunner:
    def __init__(self, packages: list[dict[str, str]]) -> None:
        self.packages = packages
        self.calls = 0

    def run(self, spec, **_kwargs):
        self.calls += 1
        now = datetime.now(UTC)
        payload = {
            "packages": self.packages,
            "environment": {
                "python_version": "3.13",
                "python_full_version": "3.13.15",
                "sys_platform": "win32",
                "os_name": "nt",
                "platform_machine": "AMD64",
                "platform_python_implementation": "CPython",
                "implementation_name": "cpython",
                "implementation_version": "3.13.15",
                "platform_release": "11",
                "platform_system": "Windows",
                "platform_version": "11",
                "extra": "",
            },
        }
        return CommandResult(
            spec,
            CommandFailure.NONE,
            0,
            now,
            now,
            stdout=json.dumps(payload),
        )


class RequirementsValidatorTests(unittest.TestCase):
    def test_normalizes_names_and_checks_versions_with_one_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "requirements.txt"
            manifest.write_text(
                "comfy-kitchen>=0.2.14\nmissing-package==1.0\n",
                encoding="utf-8",
            )
            runner = SnapshotRunner([{"name": "comfy_kitchen", "version": "0.2.31"}])

            report = RequirementsValidator(runner).validate("C:/venv/python.exe", manifest)  # type: ignore[arg-type]

            self.assertEqual(runner.calls, 1)
            self.assertFalse(report.healthy)
            self.assertEqual(report.checks[0].state, RequirementState.SATISFIED)
            self.assertEqual(report.checks[1].state, RequirementState.MISSING)

    def test_follows_includes_and_evaluates_target_markers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "more.txt").write_text(
                "windows-only; sys_platform == 'win32'\n"
                "linux-only; sys_platform == 'linux'\n",
                encoding="utf-8",
            )
            manifest = root / "requirements.txt"
            manifest.write_text("-r more.txt\n--extra-index-url https://example.invalid\n")
            runner = SnapshotRunner([{"name": "windows_only", "version": "1.0"}])

            report = RequirementsValidator(runner).validate("C:/venv/python.exe", manifest)  # type: ignore[arg-type]

            self.assertTrue(report.healthy)
            self.assertEqual(
                [check.state for check in report.checks],
                [RequirementState.SATISFIED, RequirementState.SKIPPED],
            )

    def test_reports_recursive_include_without_running_target_python(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "requirements.txt"
            manifest.write_text("-r requirements.txt\n", encoding="utf-8")
            runner = SnapshotRunner([])

            report = RequirementsValidator(runner).validate("C:/venv/python.exe", manifest)  # type: ignore[arg-type]

            self.assertFalse(report.healthy)
            self.assertIn("Recursive", report.errors[0])
            self.assertEqual(runner.calls, 0)


if __name__ == "__main__":
    unittest.main()
