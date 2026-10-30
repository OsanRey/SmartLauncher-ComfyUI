from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from smart_launcher.domain.runtimes import RuntimeProbe


class EnvironmentState(str, Enum):
    MISSING = "missing"
    EXECUTABLE_MISSING = "executable_missing"
    CONFIG_BROKEN = "config_broken"
    UNTRUSTED_BASE = "untrusted_base"
    EXECUTABLE_BROKEN = "executable_broken"
    NOT_A_VENV = "not_a_venv"
    INCOMPATIBLE_RUNTIME = "incompatible_runtime"
    PIP_BROKEN = "pip_broken"
    HEALTHY = "healthy"


class RepairStage(str, Enum):
    PREPARE = "prepare"
    BACKUP = "backup"
    CREATE = "create"
    PROVISION = "provision"
    VALIDATE = "validate"
    COMMIT = "commit"
    ROLLBACK = "rollback"


@dataclass(frozen=True, slots=True)
class EnvironmentHealth:
    root: Path
    python: Path
    state: EnvironmentState
    detail: str
    probe: RuntimeProbe | None = None

    @property
    def healthy(self) -> bool:
        return self.state is EnvironmentState.HEALTHY

    def as_dict(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "python": str(self.python),
            "state": self.state.value,
            "detail": self.detail,
            "healthy": self.healthy,
            "probe": self.probe.as_dict() if self.probe else None,
        }


@dataclass(frozen=True, slots=True)
class RepairResult:
    succeeded: bool
    stage: RepairStage
    before: EnvironmentHealth
    after: EnvironmentHealth | None
    backup: Path | None
    detail: str
    rolled_back: bool = False
