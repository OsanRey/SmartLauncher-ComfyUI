from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ServerState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    STOPPING = "stopping"
    FAILED = "failed"


class WebSocketState(str, Enum):
    STOPPED = "stopped"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RETRYING = "retrying"


class ComfyEventType(str, Enum):
    STATUS = "status"
    EXECUTION_START = "execution_start"
    EXECUTION_CACHED = "execution_cached"
    EXECUTING = "executing"
    PROGRESS = "progress"
    PROGRESS_STATE = "progress_state"
    EXECUTED = "executed"
    EXECUTION_SUCCESS = "execution_success"
    EXECUTION_ERROR = "execution_error"
    EXECUTION_INTERRUPTED = "execution_interrupted"
    UNKNOWN = "unknown"


TERMINAL_COMFY_EVENTS = frozenset(
    {
        ComfyEventType.EXECUTION_SUCCESS,
        ComfyEventType.EXECUTION_ERROR,
        ComfyEventType.EXECUTION_INTERRUPTED,
    }
)


@dataclass(frozen=True, slots=True)
class ComfyEvent:
    type: ComfyEventType
    prompt_id: str | None
    data: dict[str, Any]
    raw_type: str


@dataclass(frozen=True, slots=True)
class GenerationProgress:
    prompt_id: str
    node: str | None
    value: int
    maximum: int

    @property
    def fraction(self) -> float:
        if self.maximum <= 0:
            return 0.0
        return min(1.0, max(0.0, self.value / self.maximum))
