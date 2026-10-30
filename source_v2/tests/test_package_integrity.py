import base64
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from smart_launcher.runtime.package_integrity import PROBE
from smart_launcher.infrastructure.update_marker import UpdateMarker
from smart_launcher.domain.updates import UpdateChannel, UpdatePlan


class IntegrityTests(unittest.TestCase):
    def test_pip_installed_hashless_wheel(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            info = "demo_integrity-1.0.dist-info"
            files = {
                "demo.py": "value = 1\n",
                f"{info}/METADATA": "Metadata-Version: 2.1\nName: demo-integrity\nVersion: 1.0\n",
                f"{info}/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
            }
            files[f"{info}/RECORD"] = "".join(f"{name},,\n" for name in [*files, f"{info}/RECORD"])
            wheel = root / "demo_integrity-1.0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                for name, content in files.items():
                    archive.writestr(name, content)
            target = root / "installed"
            report = root / "report.json"
            install = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--no-index", "--no-deps",
                 "--disable-pip-version-check", "--target", str(target), "--report", str(report), str(wheel)],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(install.returncode, 0, install.stderr)
            result = subprocess.run(
                [sys.executable, "-I", "-B", "-c",
                 f"import sys; sys.path.insert(0, {str(target)!r})\n" + PROBE, str(report)],
                capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertGreater(json.loads(result.stdout)["unhashed"], 0)

    def test_hashless_record_checks_files_without_rejecting_valid_package(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            info = root / "demo_integrity-1.0.dist-info"
            info.mkdir()
            (info / "METADATA").write_text("Name: demo-integrity\nVersion: 1.0\n", encoding="utf-8")
            file = root / "demo.py"
            file.write_bytes(b"value = 1\n")
            record = info / "RECORD"
            entries = "demo.py,,10\ndemo_integrity-1.0.dist-info\\METADATA,,\ndemo_integrity-1.0.dist-info\\RECORD,,\n__pycache__/demo.pyc,,\n"
            record.write_text(entries, encoding="utf-8")
            report = root / "report.json"
            report.write_text(json.dumps({"version": "1", "install": [{"metadata": {"name": "demo-integrity"}}]}))

            def run():
                result = subprocess.run(
                    [sys.executable, "-I", "-B", "-c",
                     f"import sys; sys.path.insert(0, {str(root)!r})\n" + PROBE, str(report)],
                    capture_output=True, text=True, timeout=20,
                )
                return result.returncode, json.loads(result.stdout)

            code, result = run()
            self.assertEqual(code, 0, result)
            self.assertEqual(result["checked"], 3)
            self.assertEqual(result["unhashed"], 2)
            self.assertTrue(result["warnings"])
            file.write_bytes(b"\0" * 10)
            code, result = run()
            self.assertEqual(code, 1)
            self.assertIn("null bytes", str(result["errors"]))
            file.write_bytes(b"short")
            code, result = run()
            self.assertEqual(code, 1)
            self.assertIn("size mismatch", str(result["errors"]))
            file.unlink()
            self.assertEqual(run()[0], 1)
            record.unlink()
            code, result = run()
            self.assertEqual(code, 0, result)
            self.assertIn("RECORD absent", str(result["warnings"]))

    def test_checksum_missing_file_and_broken_record(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            info = root / "demo_integrity-1.0.dist-info"
            info.mkdir()
            (info / "METADATA").write_text("Name: demo-integrity\nVersion: 1.0\n", encoding="utf-8")
            file = root / "demo.py"
            file.write_bytes(b"value = 1\n")
            digest = base64.urlsafe_b64encode(hashlib.sha256(file.read_bytes()).digest()).decode().rstrip("=")
            record = info / "RECORD"
            record.write_text(f"demo.py,sha256={digest},{file.stat().st_size}\n", encoding="utf-8")
            report = root / "report.json"
            report.write_text(json.dumps({"version": "1", "install": [{"metadata": {"name": "demo-integrity"}}]}))
            def run():
                return subprocess.run(
                    [sys.executable, "-I", "-B", "-c",
                     f"import sys; sys.path.insert(0, {str(root)!r})\n" + PROBE, str(report)],
                    capture_output=True, text=True, timeout=20,
                )
            self.assertEqual(run().returncode, 0)
            file.write_bytes(b"\0" * 10)
            result = run()
            self.assertEqual(result.returncode, 1)
            self.assertIn("checksum mismatch", result.stdout)
            file.unlink()
            self.assertEqual(run().returncode, 1)
            record.write_bytes(b"\0" * 50)
            self.assertEqual(run().returncode, 1)
            report.write_text("{}")
            self.assertEqual(run().returncode, 1)

    def test_marker_survives_new_instance_and_corrupt_contents(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "ComfyUI"
            root.mkdir()
            plan = UpdatePlan(root, UpdateChannel.STABLE, "before", "after", "v2")
            marker = UpdateMarker(root)
            self.assertFalse(marker.pending)
            marker.begin(plan)
            self.assertTrue(UpdateMarker(root).pending)
            self.assertEqual(json.loads(marker.path.read_text())["target_commit"], "after")
            marker.path.write_bytes(b"\0" * 100)
            self.assertTrue(UpdateMarker(root).pending)
            marker.complete()
            self.assertFalse(marker.pending)
