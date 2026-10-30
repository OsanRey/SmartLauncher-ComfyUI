from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from smart_launcher.domain.updates import UpdateChannel


@dataclass(frozen=True, slots=True)
class LauncherSettings:
    comfy_root: Path
    venv_path: Path
    base_python: Path | None = None
    host: str = "127.0.0.1"
    port: int = 8188
    lan_enabled: bool = False
    manager_enabled: bool = True
    auto_open_browser: bool = False
    update_channel: UpdateChannel = UpdateChannel.STABLE
    extra_arguments: tuple[str, ...] = ()
    torch_packages: tuple[str, ...] = ("torch", "torchvision", "torchaudio")
    torch_index_url: str | None = "https://download.pytorch.org/whl/cu130"
    torch_require_cuda: bool = True
    torch_cuda_major: int | None = 13
    civitai_token: str | None = None
    huggingface_token: str | None = None

    def __post_init__(self) -> None:
        if not self.host.strip():
            raise ValueError("host cannot be empty")
        if not 1 <= self.port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        if any(not argument for argument in self.extra_arguments):
            raise ValueError("extra arguments cannot contain empty values")
        if any(not package for package in self.torch_packages):
            raise ValueError("torch packages cannot contain empty values")
        if self.torch_cuda_major is not None and self.torch_cuda_major <= 0:
            raise ValueError("torch CUDA major must be positive")
        if not self.torch_require_cuda and self.torch_cuda_major is not None:
            raise ValueError("CPU profile cannot declare a CUDA major version")

    @property
    def python_executable(self) -> Path:
        scripts = "Scripts" if os.name == "nt" else "bin"
        name = "python.exe" if os.name == "nt" else "python"
        return self.venv_path / scripts / name

    @property
    def listen_host(self) -> str:
        return "0.0.0.0" if self.lan_enabled else self.host

    @property
    def launch_arguments(self) -> tuple[str, ...]:
        manager = ("--enable-manager",) if self.manager_enabled else ()
        return (*manager, *self.extra_arguments)

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "comfy_root": str(self.comfy_root),
            "venv_path": str(self.venv_path),
            "base_python": str(self.base_python) if self.base_python else None,
            "host": self.host,
            "port": self.port,
            "lan_enabled": self.lan_enabled,
            "manager_enabled": self.manager_enabled,
            "auto_open_browser": self.auto_open_browser,
            "update_channel": self.update_channel.value,
            "extra_arguments": list(self.extra_arguments),
            "torch_packages": list(self.torch_packages),
            "torch_index_url": self.torch_index_url,
            "torch_require_cuda": self.torch_require_cuda,
            "torch_cuda_major": self.torch_cuda_major,
            "civitai_token": self.civitai_token,
            "huggingface_token": self.huggingface_token,
        }
