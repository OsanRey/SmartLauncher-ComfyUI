from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from smart_launcher.comfy.repository import ComfyRepository
from smart_launcher.domain.dependencies import TorchExpectation
from smart_launcher.domain.runtimes import RuntimeCandidate, RuntimeSource
from smart_launcher.domain.settings import LauncherSettings
from smart_launcher.domain.updates import UpdateChannel
from smart_launcher.runtime.python_resolver import PythonResolver
from smart_launcher.runtime.requirements import RequirementsValidator
from smart_launcher.runtime.torch_profile import TorchProfileValidator
from smart_launcher.runtime.venv_manager import VenvManager


class ExistingInstallationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ExistingInstallationProfile:
    settings: LauncherSettings
    detail: str


class ExistingInstallationService:
    """Validates an explicitly selected installation before it becomes launcher state."""

    def __init__(
        self,
        resolver: PythonResolver | None = None,
        environments: VenvManager | None = None,
        requirements: RequirementsValidator | None = None,
        torch: TorchProfileValidator | None = None,
    ) -> None:
        self.resolver = resolver or PythonResolver()
        self.environments = environments or VenvManager()
        self.requirements = requirements or RequirementsValidator()
        self.torch = torch or TorchProfileValidator()

    def connect(
        self,
        root: str | Path,
        *,
        expectation: TorchExpectation = TorchExpectation(),
        channel: UpdateChannel = UpdateChannel.STABLE,
    ) -> ExistingInstallationProfile:
        target = Path(os.path.abspath(Path(root).expanduser()))
        self._validate_target(target)

        repository = ComfyRepository(target).inspect()
        if not repository.valid:
            raise ExistingInstallationError(
                "Выбранная папка не является самостоятельным Git-репозиторием ComfyUI: "
                + (repository.error or "неизвестная ошибка Git")
            )

        environment = target / "venv"
        base = self._read_base(environment / "pyvenv.cfg")
        base_python = base / ("python.exe" if os.name == "nt" else "bin/python")
        base_probe = self.resolver.probe(
            RuntimeCandidate(base_python, RuntimeSource.CONFIGURED, "existing-venv-base")
        )
        if not base_probe.valid:
            raise ExistingInstallationError(
                f"Базовый Python существующего окружения недоступен: {base_python}. "
                f"{base_probe.error or ''}".strip()
            )

        health = self.environments.inspect(environment, trusted_base=base)
        if not health.healthy:
            raise ExistingInstallationError(
                f"Изолированное окружение не прошло проверку: {health.detail}"
            )

        manifests = tuple(
            target / name
            for name in ComfyRepository.MANIFESTS
            if (target / name).is_file()
        )
        for manifest in manifests:
            report = self.requirements.validate(health.python, manifest)
            if not report.healthy:
                raise ExistingInstallationError(
                    f"Пакеты окружения не соответствуют {manifest.name}. "
                    "Сначала восстановите окружение или выполните новую установку."
                )
        torch = self.torch.validate(health.python, expectation)
        if not torch.compatible:
            raise ExistingInstallationError(
                "Профиль PyTorch не соответствует выбранному режиму: "
                + "; ".join(torch.errors)
            )

        settings = LauncherSettings(
            comfy_root=target,
            venv_path=environment,
            base_python=base_probe.executable,
            update_channel=channel,
            torch_index_url=(
                "https://download.pytorch.org/whl/cu130" if expectation.require_cuda else None
            ),
            torch_require_cuda=expectation.require_cuda,
            torch_cuda_major=expectation.cuda_major,
        )
        return ExistingInstallationProfile(
            settings,
            f"Подключён ComfyUI: {target}",
        )

    @staticmethod
    def _validate_target(target: Path) -> None:
        is_junction = getattr(target, "is_junction", lambda: False)
        if target.is_symlink() or is_junction():
            raise ExistingInstallationError("Нельзя подключить ссылку или junction.")
        missing = [name for name in ("main.py", "requirements.txt") if not (target / name).is_file()]
        if missing:
            raise ExistingInstallationError(
                "В выбранной папке нет файлов ComfyUI: " + ", ".join(missing)
            )

    @staticmethod
    def _read_base(config: Path) -> Path:
        try:
            for line in config.read_text(encoding="utf-8", errors="replace").splitlines():
                key, separator, value = line.partition("=")
                if separator and key.strip().casefold() == "home" and value.strip():
                    return Path(value.strip()).resolve()
        except OSError as exc:
            raise ExistingInstallationError(f"Не удалось прочитать {config}: {exc}") from exc
        raise ExistingInstallationError(f"В {config} отсутствует путь базового Python.")
