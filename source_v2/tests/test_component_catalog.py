from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from smart_launcher.application.component_catalog import ComponentCatalog
from smart_launcher.domain.settings import LauncherSettings


class ComponentCatalogTests(unittest.TestCase):
    def test_reports_required_and_optional_components_without_running_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "ComfyUI"
            venv = root / "venv"
            python = venv / "Scripts" / "python.exe"
            python.parent.mkdir(parents=True)
            (root / "main.py").write_text("", encoding="utf-8")
            (venv / "pyvenv.cfg").write_text("home = C:/Python313\n", encoding="utf-8")
            python.write_bytes(b"")
            tools = {
                "git": "C:/Tools/Git/git.exe",
                "nvidia-smi": "C:/Windows/System32/nvidia-smi.exe",
            }

            result = ComponentCatalog(which=tools.get).inspect(
                LauncherSettings(comfy_root=root, venv_path=venv)
            )

        by_name = {item.name: item for item in result}
        self.assertEqual(by_name["ComfyUI"].status, "Готово")
        self.assertEqual(by_name["Python-окружение"].status, "Готово")
        self.assertEqual(by_name["Git"].requirement, "Для установки и обновлений")
        self.assertEqual(by_name["Git"].status, "Найден")
        self.assertEqual(by_name["CUDA Toolkit"].status, "Не требуется")
        self.assertEqual(by_name["FFmpeg"].status, "Не установлен")

    def test_cpu_profile_does_not_require_nvidia_driver(self) -> None:
        settings = LauncherSettings(
            comfy_root=Path("C:/ComfyUI"),
            venv_path=Path("C:/ComfyUI/venv"),
            torch_index_url=None,
            torch_require_cuda=False,
            torch_cuda_major=None,
        )

        result = ComponentCatalog(which=lambda _name: None).inspect(settings)

        nvidia = next(item for item in result if item.name == "Драйвер NVIDIA")
        self.assertEqual(nvidia.requirement, "Не требуется для CPU")
        self.assertEqual(nvidia.status, "Не требуется")
