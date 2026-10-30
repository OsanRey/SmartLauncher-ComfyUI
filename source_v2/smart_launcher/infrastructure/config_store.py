from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from smart_launcher.domain.settings import LauncherSettings
from smart_launcher.domain.updates import UpdateChannel


class ConfigError(RuntimeError):
    pass


class ConfigStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def load(self) -> LauncherSettings:
        if not self.path.exists():
            raise ConfigError(f"Configuration does not exist: {self.path}")
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ConfigError(f"Cannot read configuration: {exc}") from exc
        if not isinstance(payload, dict):
            raise ConfigError("Configuration root must be a JSON object.")
        return self._decode(payload, self.path.parent)

    def load_optional(self) -> LauncherSettings | None:
        """Return no profile for a genuine first run; never guess an installation path."""
        if not self.path.exists():
            return None
        return self.load()

    def save(self, settings: LauncherSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(settings.as_dict(), ensure_ascii=False, indent=2) + "\n"
        temporary: Path | None = None
        try:
            descriptor, name = tempfile.mkstemp(
                prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
            )
            temporary = Path(name)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except OSError as exc:
            raise ConfigError(f"Cannot save configuration: {exc}") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _decode(payload: dict[str, Any], base_directory: Path) -> LauncherSettings:
        version = payload.get("schema_version", 1)
        if version != 1:
            raise ConfigError(f"Unsupported configuration schema: {version}")
        try:
            root = _resolve_config_path(payload["comfy_root"], base_directory)
            venv = _resolve_config_path(payload.get("venv_path", root / "venv"), base_directory)
            arguments = payload.get("extra_arguments", [])
            if not isinstance(arguments, list) or not all(
                isinstance(argument, str) for argument in arguments
            ):
                raise TypeError("extra_arguments must be an array of strings")
            torch_packages = payload.get(
                "torch_packages", ["torch", "torchvision", "torchaudio"]
            )
            if not isinstance(torch_packages, list) or not all(
                isinstance(package, str) for package in torch_packages
            ):
                raise TypeError("torch_packages must be an array of strings")
            base_python_value = payload.get("base_python")
            if base_python_value is not None and not isinstance(base_python_value, str):
                raise TypeError("base_python must be a path string or null")
            base_python = (
                _resolve_config_path(base_python_value, base_directory)
                if base_python_value
                else None
            )
            torch_index_url = payload.get(
                "torch_index_url", "https://download.pytorch.org/whl/cu130"
            )
            if torch_index_url is not None and not isinstance(torch_index_url, str):
                raise TypeError("torch_index_url must be a string or null")
            if torch_index_url == "":
                raise ValueError("torch_index_url cannot be empty; use null to disable it")
            torch_cuda_major = payload.get("torch_cuda_major", 13)
            if torch_cuda_major is not None and (
                not isinstance(torch_cuda_major, int) or isinstance(torch_cuda_major, bool)
            ):
                raise TypeError("torch_cuda_major must be an integer or null")
            tokens = {}
            for name in ("civitai_token", "huggingface_token"):
                value = payload.get(name)
                if value is not None and not isinstance(value, str):
                    raise TypeError(f"{name} must be a string or null")
                tokens[name] = value or None
            booleans = {
                name: payload.get(name, default)
                for name, default in (
                    ("lan_enabled", False),
                    ("manager_enabled", True),
                    ("auto_open_browser", False),
                    ("torch_require_cuda", True),
                )
            }
            if not all(isinstance(value, bool) for value in booleans.values()):
                raise TypeError("boolean settings must contain true or false")
            return LauncherSettings(
                comfy_root=root,
                venv_path=venv,
                base_python=base_python,
                host=str(payload.get("host", "127.0.0.1")),
                port=int(payload.get("port", 8188)),
                lan_enabled=booleans["lan_enabled"],
                manager_enabled=booleans["manager_enabled"],
                auto_open_browser=booleans["auto_open_browser"],
                update_channel=UpdateChannel(str(payload.get("update_channel", "stable"))),
                extra_arguments=tuple(arguments),
                torch_packages=tuple(torch_packages),
                torch_index_url=torch_index_url,
                torch_require_cuda=booleans["torch_require_cuda"],
                torch_cuda_major=torch_cuda_major,
                civitai_token=tokens["civitai_token"],
                huggingface_token=tokens["huggingface_token"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError(f"Invalid configuration: {exc}") from exc


def default_config_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / "SmartLauncherComfyUI2" / "profile.json"
    return Path.home() / ".config" / "smart-launcher-comfyui2" / "profile.json"


def _resolve_config_path(value: object, base_directory: Path) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = base_directory / path
    return path.resolve()
