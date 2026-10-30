from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from smart_launcher.domain.settings import LauncherSettings
from smart_launcher.domain.updates import UpdateChannel
from smart_launcher.infrastructure.config_store import (
    ConfigError,
    ConfigStore,
    default_config_path,
)


class ConfigStoreTests(unittest.TestCase):
    def test_v2_default_profile_does_not_reuse_legacy_directory(self) -> None:
        path = default_config_path()
        self.assertEqual(path.parent.name, "SmartLauncherComfyUI2")
        self.assertEqual(path.name, "profile.json")

    def test_missing_config_is_an_explicit_unconfigured_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            store = ConfigStore(path)

            self.assertIsNone(store.load_optional())
            with self.assertRaises(ConfigError):
                store.load()
            self.assertFalse(path.exists())

    def test_round_trip_preserves_paths_profile_and_argument_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "config.json"
            settings = LauncherSettings(
                Path(directory) / "Core With Spaces",
                Path(directory) / "Runtime" / "venv",
                host="127.0.0.1",
                port=8288,
                lan_enabled=True,
                manager_enabled=False,
                update_channel=UpdateChannel.DEVELOPMENT,
                extra_arguments=("--preview-method", "auto"),
                torch_index_url=None,
                torch_require_cuda=False,
                torch_cuda_major=None,
                civitai_token="civitai-secret",
                huggingface_token="hf-secret",
            )

            store = ConfigStore(path)
            store.save(settings)

            self.assertEqual(store.load(), settings)
            self.assertFalse(any(path.parent.glob("*.tmp")))

    def test_rejects_future_schema_and_string_booleans(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps({"schema_version": 2, "comfy_root": "D:/ComfyUI"}),
                encoding="utf-8",
            )
            with self.assertRaises(ConfigError):
                ConfigStore(path).load()

            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "comfy_root": "D:/ComfyUI",
                        "lan_enabled": "false",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ConfigError):
                ConfigStore(path).load()


if __name__ == "__main__":
    unittest.main()
