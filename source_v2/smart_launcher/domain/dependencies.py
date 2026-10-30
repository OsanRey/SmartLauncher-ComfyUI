from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class RequirementState(str, Enum):
    SATISFIED = "satisfied"
    MISSING = "missing"
    INCOMPATIBLE = "incompatible"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class RequirementCheck:
    requirement: str
    name: str
    source: Path
    line: int
    state: RequirementState
    installed_version: str | None = None
    detail: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "requirement": self.requirement,
            "name": self.name,
            "source": str(self.source),
            "line": self.line,
            "state": self.state.value,
            "installed_version": self.installed_version,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class RequirementsReport:
    manifest: Path
    python: Path
    checks: tuple[RequirementCheck, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def healthy(self) -> bool:
        return not self.errors and all(
            check.state in (RequirementState.SATISFIED, RequirementState.SKIPPED)
            for check in self.checks
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "manifest": str(self.manifest),
            "python": str(self.python),
            "healthy": self.healthy,
            "checks": [check.as_dict() for check in self.checks],
            "errors": list(self.errors),
        }


@dataclass(frozen=True, slots=True)
class TorchExpectation:
    minimum_version: str = "2.7"
    require_cuda: bool = True
    cuda_major: int | None = 13


@dataclass(frozen=True, slots=True)
class TorchReport:
    python: Path
    available: bool
    compatible: bool
    torch_version: str | None = None
    cuda_version: str | None = None
    cuda_available: bool = False
    device_name: str | None = None
    errors: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "python": str(self.python),
            "available": self.available,
            "compatible": self.compatible,
            "torch_version": self.torch_version,
            "cuda_version": self.cuda_version,
            "cuda_available": self.cuda_available,
            "device_name": self.device_name,
            "errors": list(self.errors),
        }
