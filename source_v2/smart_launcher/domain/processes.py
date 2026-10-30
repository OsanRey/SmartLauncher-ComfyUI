from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class ProcessState(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ProcessSession:
    session_id: str
    pid: int
    state: ProcessState
    started_at: datetime
    tree_controlled: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "pid": self.pid,
            "state": self.state.value,
            "started_at": self.started_at.isoformat(),
            "tree_controlled": self.tree_controlled,
        }


@dataclass(frozen=True, slots=True)
class ProcessExit:
    session_id: str
    pid: int
    return_code: int
    started_at: datetime
    finished_at: datetime
    stop_requested: bool

    @property
    def duration_seconds(self) -> float:
        return max(0.0, (self.finished_at - self.started_at).total_seconds())
