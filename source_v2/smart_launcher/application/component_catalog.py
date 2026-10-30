from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from smart_launcher.domain.settings import LauncherSettings


@dataclass(frozen=True, slots=True)
class ComponentInfo:
    name: str
    requirement: str
    status: str
    detail: str
    tone: str


class ComponentCatalog:
    """Builds a lightweight system-component snapshot without running installers."""

    def __init__(self, *, which: Callable[[str], str | None] = shutil.which) -> None:
        self._which = which

    def inspect(self, settings: LauncherSettings) -> tuple[ComponentInfo, ...]:
        core_ready = (settings.comfy_root / "main.py").is_file()
        python_ready = (
            settings.python_executable.is_file()
            and (settings.venv_path / "pyvenv.cfg").is_file()
        )
        git = self._which("git")
        ffmpeg = self._which("ffmpeg")
        nvidia_smi = self._which("nvidia-smi")
        python_manager = self._which("pymanager") or self._which("py")
        build_tools = self._find_build_tools()

        items = [
            self._path_component(
                "ComfyUI",
                "Обязательно",
                core_ready,
                settings.comfy_root,
                "Ядро устанавливается и обновляется лаунчером.",
            ),
            self._path_component(
                "Python-окружение",
                "Обязательно",
                python_ready,
                settings.python_executable,
                "Изолированный venv с Python, PyTorch и пакетами ComfyUI.",
            ),
            ComponentInfo(
                "PyTorch и зависимости",
                "Обязательно",
                "Управляется лаунчером" if python_ready else "Окружение отсутствует",
                "Полная совместимость проверяется отдельной командой «Проверить окружение».",
                "good" if python_ready else "bad",
            ),
            self._tool_component(
                "Git",
                "Для установки и обновлений",
                git,
                "Не нужен для обычного запуска уже установленного ComfyUI.",
            ),
        ]

        if settings.torch_require_cuda:
            items.append(
                self._tool_component(
                    "Драйвер NVIDIA",
                    "Обязательно для CUDA",
                    nvidia_smi,
                    "CUDA Toolkit отдельно не требуется: нужный runtime входит в PyTorch.",
                )
            )
        else:
            items.append(
                ComponentInfo(
                    "Драйвер NVIDIA",
                    "Не требуется для CPU",
                    "Не требуется",
                    "Текущий профиль использует CPU.",
                    "neutral",
                )
            )

        items.extend(
            (
                self._optional_component(
                    "FFmpeg",
                    ffmpeg,
                    "Нужен отдельным видео- и аудио-нодам, но не базовому ComfyUI.",
                ),
                ComponentInfo(
                    "CUDA Toolkit",
                    "Опционально",
                    "Не требуется",
                    "Стандартный профиль использует CUDA runtime из готовых колёс PyTorch.",
                    "neutral",
                ),
                self._optional_component(
                    "C++ Build Tools",
                    build_tools,
                    "Нужны только нодам, которые компилируют расширения из исходников.",
                ),
                self._optional_component(
                    "Python Install Manager",
                    python_manager,
                    "Упрощает поиск Python; выбранного вручную совместимого Python достаточно.",
                ),
            )
        )
        return tuple(items)

    @staticmethod
    def _path_component(
        name: str,
        requirement: str,
        ready: bool,
        path: Path,
        detail: str,
    ) -> ComponentInfo:
        return ComponentInfo(
            name,
            requirement,
            "Готово" if ready else "Не найдено",
            f"{detail}\n{path}",
            "good" if ready else "bad",
        )

    @staticmethod
    def _tool_component(
        name: str,
        requirement: str,
        executable: str | None,
        detail: str,
    ) -> ComponentInfo:
        return ComponentInfo(
            name,
            requirement,
            "Найден" if executable else "Не найден",
            f"{detail}\n{executable}" if executable else detail,
            "good" if executable else "bad",
        )

    @staticmethod
    def _optional_component(
        name: str, executable: str | None, detail: str
    ) -> ComponentInfo:
        return ComponentInfo(
            name,
            "Опционально",
            "Найден" if executable else "Не установлен",
            f"{detail}\n{executable}" if executable else detail,
            "good" if executable else "neutral",
        )

    def _find_build_tools(self) -> str | None:
        compiler = self._which("cl")
        if compiler:
            return compiler
        program_files = os.environ.get("ProgramFiles(x86)")
        if not program_files:
            return None
        vswhere = Path(program_files) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
        return str(vswhere) if vswhere.is_file() else None
