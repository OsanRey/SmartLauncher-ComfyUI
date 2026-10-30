from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from smart_launcher.domain.updates import UpdatePlan


class MaintenanceStage(str, Enum):
    PREFLIGHT = "preflight"
    PLAN = "plan"
    SWITCH_CORE = "switch_core"
    SYNC_ENVIRONMENT = "sync_environment"
    VALIDATE = "validate"
    COMMIT = "commit"
    ROLLBACK = "rollback"


class MaintenanceMode(str, Enum):
    UPDATE_CHECK = "update_check"
    CORE_UPDATE = "core_update"
    ENVIRONMENT_REPAIR = "environment_repair"


@dataclass(frozen=True, slots=True)
class MaintenanceResult:
    succeeded: bool
    stage: MaintenanceStage
    detail: str
    plan: UpdatePlan | None = None
    core_rolled_back: bool = False
    environment_rolled_back: bool = False
    recovery_backup: Path | None = None


@dataclass(frozen=True, slots=True)
class DependencySyncResult:
    succeeded: bool
    installed_manifests: tuple[Path, ...]
    detail: str
