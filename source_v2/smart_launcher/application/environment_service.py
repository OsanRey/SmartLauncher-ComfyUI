from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from smart_launcher.domain.dependencies import TorchExpectation
from smart_launcher.domain.environments import EnvironmentHealth, RepairResult
from smart_launcher.domain.runtimes import RuntimeProbe
from smart_launcher.infrastructure.command_runner import ChunkCallback
from smart_launcher.runtime.dependency_sync import DependencySynchronizer
from smart_launcher.runtime.requirements import RequirementsValidator
from smart_launcher.runtime.torch_profile import TorchProfileValidator
from smart_launcher.runtime.venv_manager import VenvManager

EnvironmentValidator = Callable[[Path], tuple[bool, str]]


class ComfyEnvironmentService:
    """Owns the one canonical environment provisioning and validation policy."""

    def __init__(
        self,
        environments: VenvManager,
        dependencies: DependencySynchronizer,
        requirements: RequirementsValidator,
        torch: TorchProfileValidator,
    ) -> None:
        self.environments = environments
        self.dependencies = dependencies
        self.requirements = requirements
        self.torch = torch

    def inspect(self, environment: Path, runtime: RuntimeProbe) -> EnvironmentHealth:
        trusted_base = runtime.prefix or runtime.base_prefix or runtime.executable.parent
        return self.environments.inspect(environment, trusted_base=trusted_base)

    def rebuild(
        self,
        environment: Path,
        runtime: RuntimeProbe,
        manifests: tuple[Path, ...],
        *,
        expectation: TorchExpectation = TorchExpectation(),
        torch_packages: tuple[str, ...] = ("torch", "torchvision", "torchaudio"),
        torch_index_url: str | None = None,
        on_chunk: ChunkCallback | None = None,
        cancel_event: threading.Event | None = None,
        keep_backup: bool = False,
        validate: EnvironmentValidator | None = None,
    ) -> RepairResult:
        def provision(python: Path):
            synced = self.dependencies.sync(
                python,
                manifests,
                torch_packages=torch_packages,
                torch_index_url=torch_index_url,
                on_chunk=on_chunk,
                cancel_event=cancel_event,
            )
            if synced.succeeded:
                return None
            raise RuntimeError(synced.detail)

        return self.environments.recreate(
            environment,
            runtime,
            provision=provision,
            validate=validate or (lambda python: self.validate(python, manifests, expectation)),
            on_chunk=on_chunk,
            cancel_event=cancel_event,
            keep_backup=keep_backup,
        )

    def validate(
        self,
        python: Path,
        manifests: tuple[Path, ...],
        expectation: TorchExpectation = TorchExpectation(),
    ) -> tuple[bool, str]:
        for manifest in manifests:
            report = self.requirements.validate(python, manifest)
            if not report.healthy:
                return False, f"Requirements validation failed for {manifest.name}."
        torch = self.torch.validate(python, expectation)
        if not torch.compatible:
            return False, "; ".join(torch.errors) or "PyTorch profile is incompatible."
        return True, "Dependencies and PyTorch profile are valid."

    def finalize_recovery(self, environment: Path, backup: Path) -> tuple[bool, str]:
        return self.environments.finalize_recovery(environment, backup)
