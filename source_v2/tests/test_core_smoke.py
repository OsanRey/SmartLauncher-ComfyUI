from __future__ import annotations

import tempfile
import unittest
import sys
from datetime import UTC, datetime
from pathlib import Path

from smart_launcher.domain.commands import CommandFailure, CommandResult
from smart_launcher.runtime.core_smoke import ComfyCoreSmokeValidator


class _Runner:
    def __init__(self, succeeds: bool = True) -> None:
        self.succeeds = succeeds
        self.spec = None
        self.kwargs = None

    def run(self, spec, **kwargs):
        self.spec = spec
        self.kwargs = kwargs
        now = datetime.now(UTC)
        return CommandResult(
            spec,
            CommandFailure.NONE if self.succeeds else CommandFailure.NONZERO_EXIT,
            0 if self.succeeds else 1,
            now,
            now,
            stderr="broken import" if not self.succeeds else "",
        )


class CoreSmokeTests(unittest.TestCase):
    def test_runs_bounded_quick_test_without_custom_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.py").write_text("", encoding="utf-8")
            runner = _Runner()
            validator = ComfyCoreSmokeValidator(runner)  # type: ignore[arg-type]

            valid, _ = validator.validate(root / "venv" / "Scripts" / "python.exe", root)

            self.assertTrue(valid)
            self.assertIn("--quick-test-for-ci", runner.spec.argv)
            self.assertIn("--disable-all-custom-nodes", runner.spec.argv)
            self.assertEqual(runner.spec.cwd, root.resolve())
            self.assertIsNotNone(runner.spec.timeout)
            self.assertIsNotNone(runner.spec.idle_timeout)
            self.assertNotIn("on_chunk", runner.kwargs)

    def test_missing_entry_point_fails_without_starting_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = _Runner()
            validator = ComfyCoreSmokeValidator(runner)  # type: ignore[arg-type]

            valid, detail = validator.validate(Path(directory) / "python.exe", Path(directory))

            self.assertFalse(valid)
            self.assertIn("entry point", detail)
            self.assertIsNone(runner.spec)

    def test_real_probe_can_import_package_from_core_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "comfy"
            package.mkdir()
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "options.py").write_text("READY = True\n", encoding="utf-8")
            (root / "main.py").write_text(
                "import comfy.options\nassert comfy.options.READY\n",
                encoding="utf-8",
            )

            valid, detail = ComfyCoreSmokeValidator().validate(Path(sys.executable), root)

            self.assertTrue(valid, detail)


if __name__ == "__main__":
    unittest.main()
