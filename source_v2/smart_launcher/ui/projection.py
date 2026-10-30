from __future__ import annotations

from dataclasses import dataclass

from smart_launcher.domain.events import JournalEntry, LogRecord, ProgressState
from smart_launcher.infrastructure.stores import EventJournal, LogStore, ProgressStore


@dataclass(frozen=True, slots=True)
class ProjectionBatch:
    logs: tuple[LogRecord, ...]
    events: tuple[JournalEntry, ...]
    progress: tuple[ProgressState, ...]
    progress_changed: bool


class UiProjection:
    """Incrementally reads persistent stores without consuming their contents."""

    def __init__(
        self,
        logs: LogStore,
        events: EventJournal,
        progress: ProgressStore,
        *,
        batch_limit: int = 250,
    ) -> None:
        if batch_limit <= 0:
            raise ValueError("batch_limit must be positive")
        self._logs = logs
        self._events = events
        self._progress = progress
        self._batch_limit = batch_limit
        self._log_cursor = 0
        self._event_cursor = 0
        self._last_progress: tuple[ProgressState, ...] = ()

    def drain(self) -> ProjectionBatch:
        logs = self._logs.snapshot(after_sequence=self._log_cursor)[: self._batch_limit]
        events = self._events.snapshot(after_sequence=self._event_cursor)[: self._batch_limit]
        if logs:
            self._log_cursor = logs[-1].sequence
        if events:
            self._event_cursor = events[-1].sequence
        progress = self._progress.snapshot()
        changed = progress != self._last_progress
        self._last_progress = progress
        return ProjectionBatch(logs, events, progress, changed)

    def reset(self) -> None:
        """Rebuilds a view on its next drain without altering application history."""
        self._log_cursor = 0
        self._event_cursor = 0
        self._last_progress = ()
