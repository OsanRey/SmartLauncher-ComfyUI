from __future__ import annotations

import threading
import uuid
from collections import deque

from smart_launcher.domain.downloads import (
    DownloadQueueEntry,
    DownloadRequest,
    QueueEntryState,
)
from smart_launcher.domain.events import (
    DomainEvent,
    EventSeverity,
    ProgressChannel,
    ProgressMode,
    ProgressState,
)
from smart_launcher.downloads.transport import ModelDownloadTransport
from smart_launcher.infrastructure.stores import EventJournal, LogStore, ProgressStore


class DownloadController:
    """Owns the model queue independently from any downloader window."""

    def __init__(
        self,
        transport: ModelDownloadTransport,
        logs: LogStore,
        journal: EventJournal,
        progress: ProgressStore,
    ) -> None:
        self.transport = transport
        self.logs = logs
        self.journal = journal
        self.progress = progress
        self._lock = threading.RLock()
        self._queue: deque[DownloadQueueEntry] = deque()
        self._active: DownloadQueueEntry | None = None
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._shutdown = False

    @property
    def running(self) -> bool:
        with self._lock:
            return self._active is not None

    def snapshot(self) -> tuple[DownloadQueueEntry, ...]:
        with self._lock:
            active = (self._active,) if self._active is not None else ()
            return (*active, *tuple(self._queue))

    def enqueue(self, request: DownloadRequest) -> str:
        entry = DownloadQueueEntry(uuid.uuid4().hex, request)
        with self._lock:
            if self._shutdown:
                raise RuntimeError("Download controller is closed.")
            self._queue.append(entry)
            should_start = self._thread is None or not self._thread.is_alive()
            if should_start:
                self._thread = threading.Thread(
                    target=self._run_queue,
                    name="model-download-queue",
                    daemon=True,
                )
            self.journal.append(
                DomainEvent(
                    "DownloadQueued",
                    f"Добавлено в очередь: {request.candidate.name}",
                    operation_id=entry.operation_id,
                    data={"destination": str(request.destination)},
                )
            )
            if should_start:
                self._thread.start()
        return entry.operation_id

    def cancel_active(self) -> bool:
        with self._lock:
            if self._active is None:
                return False
            self._cancel.set()
            return True

    def clear_queue(self) -> int:
        with self._lock:
            count = len(self._queue)
            self._queue.clear()
            return count

    def close(self, timeout: float = 5.0) -> None:
        with self._lock:
            self._shutdown = True
            self._queue.clear()
            self._cancel.set()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def _run_queue(self) -> None:
        while True:
            with self._lock:
                if self._shutdown or not self._queue:
                    self._active = None
                    self._thread = None
                    return
                queued = self._queue.popleft()
                entry = DownloadQueueEntry(
                    queued.operation_id, queued.request, QueueEntryState.ACTIVE
                )
                self._active = entry
                self._cancel = threading.Event()
                cancel = self._cancel
            self._run_one(entry, cancel)
            with self._lock:
                if self._active is entry:
                    self._active = None

    def _run_one(self, entry: DownloadQueueEntry, cancel: threading.Event) -> None:
        request = entry.request
        self.journal.append(
            DomainEvent(
                "DownloadStarted",
                f"Скачивание модели: {request.candidate.name}",
                operation_id=entry.operation_id,
            )
        )
        self.progress.set(
            ProgressState(
                ProgressChannel.DOWNLOAD,
                entry.operation_id,
                f"Загрузка: {request.candidate.name}",
                ProgressMode.INDETERMINATE,
            )
        )

        def update(written: int, total: int | None) -> None:
            mode = ProgressMode.DETERMINATE if total else ProgressMode.INDETERMINATE
            self.progress.set(
                ProgressState(
                    ProgressChannel.DOWNLOAD,
                    entry.operation_id,
                    self._progress_label(request.candidate.name, written, total),
                    mode,
                    written,
                    total or 1,
                )
            )

        result = self.transport.download(
            request,
            on_progress=update,
            cancel_event=cancel,
        )
        self.progress.clear(ProgressChannel.DOWNLOAD, entry.operation_id)
        if result.succeeded:
            self.logs.append(
                source="downloader",
                stream="internal",
                text=f"Downloaded {request.candidate.name} -> {result.path}\n",
                operation_id=entry.operation_id,
            )
            self.journal.append(
                DomainEvent(
                    "DownloadCompleted",
                    f"Модель загружена: {request.candidate.name}",
                    EventSeverity.SUCCESS,
                    str(result.path or ""),
                    entry.operation_id,
                    data={"bytes": result.bytes_written},
                )
            )
            return
        self.journal.append(
            DomainEvent(
                "DownloadCancelled" if result.cancelled else "DownloadFailed",
                "Загрузка отменена" if result.cancelled else "Не удалось скачать модель",
                EventSeverity.WARNING if result.cancelled else EventSeverity.ERROR,
                result.detail,
                entry.operation_id,
            )
        )

    @staticmethod
    def _progress_label(name: str, written: int, total: int | None) -> str:
        current = written / 1024**2
        if total:
            return f"Загрузка: {name}  {current:.1f} / {total / 1024**2:.1f} MB"
        return f"Загрузка: {name}  {current:.1f} MB"
