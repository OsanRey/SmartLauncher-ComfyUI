from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from smart_launcher.application.custom_node_catalog import CustomNodeCatalog
from smart_launcher.domain.commands import CommandFailure


class _Runner:
    def __init__(self) -> None:
        self.specs = []

    def run(self, spec, **_kwargs):
        self.specs.append(spec)
        argv = spec.argv
        if "status" in argv:
            dirty = "dirty-node" in argv[2]
            output = (
                "# branch.oid " + "a" * 40 + "\n# branch.head main\n"
                + ("? generated.cache\n" if dirty else "")
            )
            return self._result(output)
        if "get-url" in argv:
            name = Path(argv[2]).name
            return self._result(f"https://github.com/owner/{name}.git\n")
        if "ls-remote" in argv:
            return self._result("b" * 40 + "\trefs/heads/main\n")
        raise AssertionError(argv)

    @staticmethod
    def _result(stdout: str):
        return SimpleNamespace(
            succeeded=True,
            failure=CommandFailure.NONE,
            stdout=stdout,
            stderr="",
            exception=None,
        )


class CustomNodeCatalogTests(unittest.TestCase):
    def test_scans_git_and_unmanaged_nodes_without_hidden_staging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            custom_nodes = root / "custom_nodes"
            for name in ("clean-node", "dirty-node"):
                (custom_nodes / name / ".git").mkdir(parents=True)
                (custom_nodes / name / "__init__.py").write_text("", encoding="utf-8")
            (custom_nodes / "manual-node").mkdir()
            (custom_nodes / "manual-node" / "__init__.py").write_text("", encoding="utf-8")
            (custom_nodes / "ComfyUI" / ".git").mkdir(parents=True)
            (custom_nodes / "ComfyUI" / "main.py").write_text("", encoding="utf-8")
            (custom_nodes / ".smart-launcher-stage-old").mkdir()
            runner = _Runner()

            entries = CustomNodeCatalog(runner).scan(root)

            self.assertEqual([entry.name for entry in entries], [
                "clean-node",
                "dirty-node",
                "manual-node",
            ])
            self.assertTrue(entries[0].managed)
            self.assertFalse(entries[0].dirty)
            self.assertTrue(entries[1].dirty)
            self.assertFalse(entries[2].managed)

    def test_remote_check_marks_update_and_skips_dirty_or_unmanaged_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            custom_nodes = root / "custom_nodes"
            for name in ("clean-node", "dirty-node"):
                (custom_nodes / name / ".git").mkdir(parents=True)
                (custom_nodes / name / "__init__.py").write_text("", encoding="utf-8")
            (custom_nodes / "manual-node").mkdir()
            (custom_nodes / "manual-node" / "__init__.py").write_text("", encoding="utf-8")
            runner = _Runner()
            catalog = CustomNodeCatalog(runner)
            entries = catalog.scan(root)

            checked = catalog.check_updates(entries)

            self.assertTrue(checked[0].update_available)
            self.assertIsNone(checked[1].update_available)
            self.assertIsNone(checked[2].update_available)
            remote_calls = [spec for spec in runner.specs if "ls-remote" in spec.argv]
            self.assertEqual(len(remote_calls), 1)
            self.assertEqual(remote_calls[0].idle_timeout, 20)

    def test_scans_through_shared_custom_nodes_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "ComfyUI"
            root.mkdir()
            shared = base / "shared-custom-nodes"
            (shared / "clean-node" / ".git").mkdir(parents=True)
            (shared / "clean-node" / "__init__.py").write_text("", encoding="utf-8")
            try:
                os.symlink(shared, root / "custom_nodes", target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlinks are unavailable: {exc}")

            entries = CustomNodeCatalog(_Runner()).scan(root)

            self.assertEqual([entry.name for entry in entries], ["clean-node"])
            self.assertTrue(entries[0].managed)

    def test_recognizes_registry_node_without_git_and_compares_versions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            node = root / "custom_nodes" / "registry-node"
            node.mkdir(parents=True)
            (node / ".tracking").write_text("pyproject.toml\n", encoding="utf-8")
            (node / "pyproject.toml").write_text(
                """[project]
name = "registry-node"
version = "1.2.0"
[project.urls]
Repository = "https://github.com/owner/registry-node"
""",
                encoding="utf-8",
            )
            cache = root / "user" / "__manager" / "cache"
            cache.mkdir(parents=True)
            (cache / "123_nodes.json").write_text(
                json.dumps({"nodes": [{
                    "id": "registry-node",
                    "latest_version": {"version": "1.3.0"},
                }]}),
                encoding="utf-8",
            )
            entries = CustomNodeCatalog(_Runner()).scan(root)

            self.assertTrue(entries[0].is_registry)
            self.assertEqual(entries[0].installed_version, "1.2.0")
            checked = CustomNodeCatalog(_Runner()).check_updates(
                entries, comfy_root=root
            )
            self.assertTrue(checked[0].update_available)
            self.assertEqual(checked[0].latest_version, "1.3.0")

    def test_registry_version_newer_than_cache_is_not_marked_outdated(self) -> None:
        from smart_launcher.domain.custom_nodes import InstalledCustomNode

        entry = InstalledCustomNode(
            "node",
            Path("node"),
            registry_id="node",
            installed_version="2.0.0",
            latest_version="1.9.0",
        )

        self.assertFalse(entry.update_available)


if __name__ == "__main__":
    unittest.main()
