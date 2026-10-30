from __future__ import annotations

import threading
import re
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime

from smart_launcher.domain.commands import StreamChunk
from smart_launcher.domain.events import (
    DomainEvent,
    JournalEntry,
    LogRecord,
    ProgressChannel,
    ProgressState,
)


_ANSI_ESCAPE = re.compile(
    r"\x1b(?:\][^\x07]*(?:\x07|\x1b\\)|\[[0-?]*[ -/]*[@-~]|[@-_])"
)


def strip_terminal_control_sequences(text: str) -> str:
    return _ANSI_ESCAPE.sub("", text)


class LogStore:
    def __init__(
        self,
        capacity: int = 10_000,
        *,
        on_append: Callable[[LogRecord], None] | None = None,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._lock = threading.RLock()
        self._records: deque[LogRecord] = deque(maxlen=capacity)
        self._sequence = 0
        self._on_append = on_append

    def append_chunk(
        self,
        chunk: StreamChunk,
        *,
        session_id: str | None = None,
        operation_id: str | None = None,
        transient: bool = False,
    ) -> LogRecord:
        return self.append(
            source=chunk.source,
            stream=chunk.stream.value,
            text=chunk.text,
            timestamp=chunk.timestamp,
            session_id=session_id,
            operation_id=operation_id,
            transient=transient,
        )

    def append(
        self,
        *,
        source: str,
        stream: str,
        text: str,
        timestamp: datetime | None = None,
        session_id: str | None = None,
        operation_id: str | None = None,
        transient: bool = False,
    ) -> LogRecord:
        with self._lock:
            self._sequence += 1
            record = LogRecord(
                self._sequence,
                timestamp or datetime.now(UTC),
                source,
                stream,
                strip_terminal_control_sequences(text),
                session_id,
                operation_id,
                transient,
            )
            self._records.append(record)
        if self._on_append is not None:
            try:
                self._on_append(record)
            except Exception:
                pass
        return record

    def session_marker(self, session_id: str, title: str) -> LogRecord:
        return self.append(
            source="launcher",
            stream="internal",
            text=f"\n--- {title} ---\n",
            session_id=session_id,
        )

    def snapshot(self, *, after_sequence: int = 0) -> tuple[LogRecord, ...]:
        with self._lock:
            return tuple(record for record in self._records if record.sequence > after_sequence)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


class EventJournal:
    def __init__(
        self,
        capacity: int = 2_000,
        *,
        on_append: Callable[[JournalEntry], None] | None = None,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._lock = threading.RLock()
        self._entries: deque[JournalEntry] = deque(maxlen=capacity)
        self._sequence = 0
        self._on_append = on_append

    def append(self, event: DomainEvent) -> JournalEntry:
        with self._lock:
            self._sequence += 1
            entry = JournalEntry(self._sequence, event)
            self._entries.append(entry)
        if self._on_append is not None:
            try:
                self._on_append(entry)
            except Exception:
                pass
        return entry

    def snapshot(self, *, after_sequence: int = 0) -> tuple[JournalEntry, ...]:
        with self._lock:
            return tuple(entry for entry in self._entries if entry.sequence > after_sequence)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


class ProgressStore:
    """Keeps one independently owned state per progress channel."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._states: dict[ProgressChannel, ProgressState] = {}

    def set(self, state: ProgressState) -> None:
        with self._lock:
            self._states[state.channel] = state

    def clear(self, channel: ProgressChannel, operation_id: str) -> bool:
        with self._lock:
            current = self._states.get(channel)
            if current is None or current.operation_id != operation_id:
                return False
            del self._states[channel]
            return True

    def get(self, channel: ProgressChannel) -> ProgressState | None:
        with self._lock:
            return self._states.get(channel)

    def snapshot(self) -> tuple[ProgressState, ...]:
        with self._lock:
            return tuple(
                self._states[channel]
                for channel in ProgressChannel
                if channel in self._states
            )


def should_follow_autoscroll(
    *, enabled: bool, distance_from_bottom: float, threshold: float = 24.0
) -> bool:
    return enabled and distance_from_bottom <= threshold
