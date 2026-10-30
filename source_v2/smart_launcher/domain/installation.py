from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from smart_launcher.domain.updates import UpdateChannel


class InstallationStage(str, Enum):
    PREFLIGHT = "preflight"
    CLONE = "clone"
    SELECT_REVISION = "select_revision"
    PLACE_CORE = "place_core"
    CREATE_ENVIRONMENT = "create_environment"
    COMMIT = "commit"
    ROLLBACK = "rollback"


@dataclass(frozen=True, slots=True)
class InstallationRequest:
    target: Path
    repository_url: str = "https://github.com/Comfy-Org/ComfyUI.git"
    channel: UpdateChannel = UpdateChannel.STABLE


@dataclass(frozen=True, slots=True)
class InstallationResult:
    succeeded: bool
    stage: InstallationStage
    detail: str
    target: Path
    commit: str | None = None
    rolled_back: bool = False
    recovery_path: Path | None = None
