from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Mapping


class EventSeverity(str, Enum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class DomainEvent:
    kind: str
    title: str
    severity: EventSeverity = EventSeverity.INFO
    detail: str = ""
    operation_id: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    data: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class JournalEntry:
    sequence: int
    event: DomainEvent


@dataclass(frozen=True, slots=True)
class LogRecord:
    sequence: int
    timestamp: datetime
    source: str
    stream: str
    text: str
    session_id: str | None = None
    operation_id: str | None = None
    transient: bool = False


class ProgressChannel(str, Enum):
    STARTUP = "startup"
    GENERATION = "generation"
    DOWNLOAD = "download"
    MAINTENANCE = "maintenance"


class ProgressMode(str, Enum):
    INDETERMINATE = "indeterminate"
    DETERMINATE = "determinate"


@dataclass(frozen=True, slots=True)
class ProgressState:
    channel: ProgressChannel
    operation_id: str
    label: str
    mode: ProgressMode
    value: float = 0.0
    maximum: float = 1.0
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def fraction(self) -> float | None:
        if self.mode is ProgressMode.INDETERMINATE:
            return None
        if self.maximum <= 0:
            return 0.0
        return min(1.0, max(0.0, self.value / self.maximum))
