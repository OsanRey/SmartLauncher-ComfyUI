from __future__ import annotations

import threading

from smart_launcher.application.custom_node_pipeline import CustomNodePipeline
from smart_launcher.application.operation_coordinator import (
    OperationCoordinator,
    OperationKind,
    OperationLease,
)
from smart_launcher.domain.custom_nodes import CustomNodeRequest, CustomNodeResult
from smart_launcher.domain.events import (
    DomainEvent,
    EventSeverity,
    ProgressChannel,
    ProgressMode,
    ProgressState,
)
from smart_launcher.infrastructure.stores import EventJournal, LogStore, ProgressStore


class CustomNodeController:
    def __init__(
        self,
        pipeline: CustomNodePipeline,
        logs: LogStore,
        journal: EventJournal,
        progress: ProgressStore,
        coordinator: OperationCoordinator,
    ) -> None:
        self.pipeline = pipeline
        self.logs = logs
        self.journal = journal
        self.progress = progress
        self.coordinator = coordinator
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._operation_id: str | None = None
        self._last_result: CustomNodeResult | None = None

    @property
    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    @property
    def last_result(self) -> CustomNodeResult | None:
        with self._lock:
            return self._last_result

    def start(self, request: CustomNodeRequest) -> bool:
        lease = self.coordinator.try_acquire(OperationKind.CUSTOM_NODES)
        if lease is None:
            return False
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                lease.release()
                return False
            self._cancel = threading.Event()
            self._operation_id = lease.snapshot.operation_id
            self._last_result = None
            thread = threading.Thread(
                target=self._guarded_run,
                args=(request, lease, self._cancel),
                name="custom-node-operation",
                daemon=True,
            )
            self._thread = thread
            try:
                thread.start()
            except Exception:
                self._thread = None
                lease.release()
                raise
        return True

    def cancel(self) -> bool:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                return False
            self._cancel.set()
            operation_id = self._operation_id
        if operation_id:
            self._set_progress(operation_id, "Отмена установки Custom Node...")
        return True

    def close(self, timeout: float = 5.0) -> None:
        self.cancel()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def _guarded_run(
        self,
        request: CustomNodeRequest,
        lease: OperationLease,
        cancel: threading.Event,
    ) -> None:
        operation_id = lease.snapshot.operation_id
        try:
            self.journal.append(
                DomainEvent(
                    "CustomNodeStarted",
                    (
                        "Удаление Custom Node"
                        if request.remove_target is not None
                        else "Восстановление зависимостей Custom Nodes"
                        if request.repair_dependencies
                        else "Установка или обновление Custom Node"
                    ),
                    operation_id=operation_id,
                    data={"repository": request.repository_url},
                )
            )
            self._set_progress(operation_id, "Подготовка Custom Node...")

            def on_chunk(chunk) -> None:
                self.logs.append_chunk(chunk, operation_id=operation_id)
                labels = {
                    "custom-node-check": "Проверка существующей ноды...",
                    "custom-node-git": "Клонирование Custom Node...",
                    "custom-node-pip": "Установка зависимостей Custom Node...",
                    "custom-node-manager": (
                        "Удаление через ComfyUI Manager..."
                        if request.remove_target is not None
                        else "Обновление через ComfyUI Manager..."
                    ),
                    "custom-node-dependencies": "Восстановление зависимостей Custom Nodes...",
                }
                label = labels.get(chunk.source)
                if label:
                    self._set_progress(operation_id, label)

            result = self.pipeline.execute(
                request, on_chunk=on_chunk, cancel_event=cancel
            )
            with self._lock:
                self._last_result = result
            kind = "CustomNodeCompleted" if result.succeeded else (
                "CustomNodeCancelled" if result.cancelled else "CustomNodeFailed"
            )
            severity = (
                EventSeverity.SUCCESS
                if result.succeeded
                else EventSeverity.WARNING
                if result.cancelled
                else EventSeverity.ERROR
            )
            self.journal.append(
                DomainEvent(
                    kind,
                    "Custom Node готов" if result.succeeded else "Custom Node не установлен",
                    severity,
                    result.detail,
                    operation_id,
                    data={"target": str(result.target or "")},
                )
            )
        except Exception as exc:
            with self._lock:
                self._last_result = CustomNodeResult(
                    False, f"{type(exc).__name__}: {exc}"
                )
            self.journal.append(
                DomainEvent(
                    "CustomNodeFailed",
                    "Custom Node не установлен",
                    EventSeverity.ERROR,
                    f"{type(exc).__name__}: {exc}",
                    operation_id,
                )
            )
        finally:
            self.progress.clear(ProgressChannel.MAINTENANCE, operation_id)
            with self._lock:
                if self._operation_id == operation_id:
                    self._thread = None
            lease.release()

    def _set_progress(self, operation_id: str, label: str) -> None:
        self.progress.set(
            ProgressState(
                ProgressChannel.MAINTENANCE,
                operation_id,
                label,
                ProgressMode.INDETERMINATE,
            )
        )
