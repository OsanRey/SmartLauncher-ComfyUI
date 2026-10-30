from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Mapping


class StreamName(str, Enum):
    STDOUT = "stdout"
    STDERR = "stderr"


class CommandFailure(str, Enum):
    NONE = "none"
    NOT_FOUND = "not_found"
    NONZERO_EXIT = "nonzero_exit"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    START_FAILED = "start_failed"


@dataclass(frozen=True, slots=True)
class CommandSpec:
    argv: tuple[str, ...]
    cwd: Path | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    source: str = "launcher"
    timeout: float | None = None
    idle_timeout: float | None = None
    stdin: bytes | None = None
    encoding: str = "utf-8"
    hide_window: bool = True

    def __post_init__(self) -> None:
        if not self.argv or not self.argv[0].strip():
            raise ValueError("argv must contain an executable")
        if self.timeout is not None and self.timeout <= 0:
            raise ValueError("timeout must be positive")
        if self.idle_timeout is not None and self.idle_timeout <= 0:
            raise ValueError("idle_timeout must be positive")


@dataclass(frozen=True, slots=True)
class StreamChunk:
    sequence: int
    source: str
    stream: StreamName
    text: str
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class CommandResult:
    spec: CommandSpec
    failure: CommandFailure
    return_code: int | None
    started_at: datetime
    finished_at: datetime
    stdout: str = ""
    stderr: str = ""
    exception: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.failure is CommandFailure.NONE and self.return_code == 0

    @property
    def duration_seconds(self) -> float:
        return max(0.0, (self.finished_at - self.started_at).total_seconds())
