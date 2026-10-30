from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class UpdateChannel(str, Enum):
    STABLE = "stable"
    DEVELOPMENT = "development"


@dataclass(frozen=True, slots=True)
class RepositoryState:
    root: Path
    valid: bool
    head: str | None = None
    branch: str | None = None
    dirty_paths: tuple[str, ...] = ()
    error: str | None = None

    @property
    def clean(self) -> bool:
        return self.valid and not self.dirty_paths

    def as_dict(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "valid": self.valid,
            "head": self.head,
            "branch": self.branch,
            "dirty_paths": list(self.dirty_paths),
            "clean": self.clean,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class UpdatePlan:
    root: Path
    channel: UpdateChannel
    current_commit: str
    target_commit: str
    target_ref: str
    changed_manifests: tuple[str, ...] = ()
    current_label: str = ""
    changes: tuple[str, ...] = ()
    requirements_diff: str = ""
    compare_url: str = ""

    @property
    def version_summary(self) -> str:
        current = self.current_label or self.current_commit[:12]
        target = self.target_ref.removeprefix("refs/tags/")
        return f"{current} → {target} ({self.target_commit[:12]})"

    @property
    def dependency_summary(self) -> str:
        return (
            "Изменены требования к Python-пакетам: " + ", ".join(self.changed_manifests)
            if self.changed_manifests
            else "Требования к Python-пакетам не изменились."
        )

    @property
    def update_available(self) -> bool:
        return self.current_commit != self.target_commit

    def as_dict(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "channel": self.channel.value,
            "current_commit": self.current_commit,
            "target_commit": self.target_commit,
            "target_ref": self.target_ref,
            "changed_manifests": list(self.changed_manifests),
            "update_available": self.update_available,
            "current_label": self.current_label,
            "changes": list(self.changes),
            "requirements_diff": self.requirements_diff,
            "compare_url": self.compare_url,
        }


@dataclass(frozen=True, slots=True)
class UpdateResult:
    succeeded: bool
    plan: UpdatePlan
    final_commit: str | None
    detail: str
    rolled_back: bool = False
