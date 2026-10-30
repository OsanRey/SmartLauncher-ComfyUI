from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from smart_launcher.application.update_pipeline import CoreUpdatePipeline
from smart_launcher.application.operation_coordinator import (
    OperationCoordinator,
    OperationKind,
    OperationLease,
)
from smart_launcher.domain.dependencies import TorchExpectation
from smart_launcher.domain.events import (
    DomainEvent,
    EventSeverity,
    ProgressChannel,
    ProgressMode,
    ProgressState,
)
from smart_launcher.domain.maintenance import MaintenanceMode, MaintenanceResult
from smart_launcher.domain.updates import UpdateChannel, UpdatePlan
from smart_launcher.infrastructure.stores import EventJournal, LogStore, ProgressStore
from smart_launcher.runtime.python_resolver import PythonResolver


@dataclass(frozen=True, slots=True)
class MaintenanceRequest:
    channel: UpdateChannel
    environment: Path
    base_python: Path | None = None
    torch_packages: tuple[str, ...] = ("torch", "torchvision", "torchaudio")
    torch_index_url: str | None = None
    torch_expectation: TorchExpectation = TorchExpectation()
    mode: MaintenanceMode = MaintenanceMode.CORE_UPDATE
    approved_plan: UpdatePlan | None = None


class MaintenanceController:
    def __init__(
        self,
        pipeline: CoreUpdatePipeline,
        resolver: PythonResolver,
        logs: LogStore,
        journal: EventJournal,
        progress: ProgressStore,
        coordinator: OperationCoordinator | None = None,
    ) -> None:
        self.pipeline = pipeline
        self.resolver = resolver
        self.logs = logs
        self.journal = journal
        self.progress = progress
        self.coordinator = coordinator or OperationCoordinator()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._operation_id: str | None = None
        self._last_result: MaintenanceResult | None = None
        self._available_plan: UpdatePlan | None = None

    @property
    def available_plan(self) -> UpdatePlan | None:
        with self._lock:
            return self._available_plan

    @property
    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    @property
    def last_result(self) -> MaintenanceResult | None:
        with self._lock:
            return self._last_result

    def start(self, request: MaintenanceRequest) -> bool:
        lease = self.coordinator.try_acquire(OperationKind.MAINTENANCE)
        if lease is None:
            return False
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                lease.release()
                return False
            operation_id = lease.snapshot.operation_id
            self._cancel = threading.Event()
            self._operation_id = operation_id
            self._last_result = None
            thread = threading.Thread(
                target=self._guarded_run,
                args=(request, operation_id, self._cancel, lease),
                name="launcher-maintenance",
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
            self._set_progress(operation_id, "Отмена обслуживания...")
        return True

    def replace_pipeline(self, pipeline: CoreUpdatePipeline) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("Cannot replace the update pipeline while it is running.")
            self.pipeline = pipeline

    def close(self, timeout: float = 5.0) -> None:
        self.cancel()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def _guarded_run(
        self,
        request: MaintenanceRequest,
        operation_id: str,
        cancel: threading.Event,
        lease: OperationLease,
    ) -> None:
        try:
            self._run(request, operation_id, cancel)
        except Exception as exc:
            self._finish_failure(operation_id, f"{type(exc).__name__}: {exc}")
        finally:
            self.progress.clear(ProgressChannel.MAINTENANCE, operation_id)
            with self._lock:
                if self._operation_id == operation_id:
                    self._thread = None
            lease.release()

    def _run(
        self,
        request: MaintenanceRequest,
        operation_id: str,
        cancel: threading.Event,
    ) -> None:
        self.journal.append(
            DomainEvent(
                "MaintenanceStarted",
                (
                    "Автоматическая проверка обновлений ComfyUI"
                    if request.mode is MaintenanceMode.UPDATE_CHECK
                    else "Обновление ComfyUI"
                    if request.mode is MaintenanceMode.CORE_UPDATE
                    else "Проверка окружения ComfyUI"
                ),
                operation_id=operation_id,
            )
        )
        if request.mode is MaintenanceMode.UPDATE_CHECK:
            self._set_progress(operation_id, "Проверка обновлений ComfyUI...")
            result = self.pipeline.check_update(
                request.channel,
                cancel_event=cancel,
            )
            self._publish_result(request, operation_id, result)
            return
        self._set_progress(operation_id, "Поиск подходящего Python...")
        configured = (request.base_python,) if request.base_python is not None else ()
        resolution = self.resolver.resolve(configured)
        if resolution.selected is None:
            detail = "; ".join(resolution.diagnostics) or "Подходящий Python не найден."
            self._finish_failure(operation_id, detail)
            return

        self._set_progress(
            operation_id,
            "Подготовка обновления ComfyUI..."
            if request.mode is MaintenanceMode.CORE_UPDATE
            else "Проверка окружения ComfyUI...",
        )

        def on_chunk(chunk) -> None:
            self.logs.append_chunk(chunk, operation_id=operation_id)
            labels = {
                "venv-create": "Создание нового окружения...",
                "pip-torch": "Установка профиля PyTorch...",
                "pip-requirements": "Синхронизация зависимостей...",
                "pip-check": "Финальная проверка пакетов...",
                "package-integrity": "Проверка целостности файлов пакетов...",
                "comfy-smoke": "Проверка запуска обновлённого ядра...",
            }
            label = labels.get(chunk.source)
            if label:
                self._set_progress(operation_id, label)

        arguments = dict(
            torch_packages=request.torch_packages,
            torch_index_url=request.torch_index_url,
            torch_expectation=request.torch_expectation,
            on_chunk=on_chunk,
            cancel_event=cancel,
        )
        result = (
            self.pipeline.execute(
                request.channel,
                request.environment,
                resolution.selected,
                approved_plan=request.approved_plan,
                **arguments,
            )
            if request.mode is MaintenanceMode.CORE_UPDATE
            else self.pipeline.repair_environment(
                request.environment,
                resolution.selected,
                **arguments,
            )
        )
        self._publish_result(request, operation_id, result)

    def _publish_result(
        self,
        request: MaintenanceRequest,
        operation_id: str,
        result: MaintenanceResult,
    ) -> None:
        with self._lock:
            self._last_result = result
            if result.succeeded and request.mode is MaintenanceMode.UPDATE_CHECK:
                self._available_plan = (
                    result.plan if result.plan and result.plan.update_available else None
                )
            elif result.succeeded and request.mode is MaintenanceMode.CORE_UPDATE:
                self._available_plan = None
            elif request.mode is MaintenanceMode.CORE_UPDATE and result.plan is not None:
                self._available_plan = result.plan if result.plan.update_available else None
        update_available = bool(
            result.succeeded
            and request.mode is MaintenanceMode.UPDATE_CHECK
            and result.plan is not None
            and result.plan.update_available
        )
        severity = (
            EventSeverity.WARNING
            if update_available
            else EventSeverity.SUCCESS
            if result.succeeded
            else EventSeverity.ERROR
        )
        title = (
            "Доступно обновление ComfyUI"
            if update_available
            else "ComfyUI актуален"
            if result.succeeded and request.mode is MaintenanceMode.UPDATE_CHECK
            else "Обновление ComfyUI завершено"
            if result.succeeded and request.mode is MaintenanceMode.CORE_UPDATE
            else "Проверка окружения завершена"
            if result.succeeded
            else "Обслуживание не выполнено"
        )
        self.journal.append(
            DomainEvent(
                (
                    "UpdateAvailable"
                    if update_available
                    else "UpdateCheckCompleted"
                    if result.succeeded and request.mode is MaintenanceMode.UPDATE_CHECK
                    else "MaintenanceCompleted"
                    if result.succeeded
                    else "MaintenanceFailed"
                ),
                title,
                severity,
                result.detail,
                operation_id,
            )
        )

    def _finish_failure(self, operation_id: str, detail: str) -> None:
        self.journal.append(
            DomainEvent(
                "MaintenanceFailed",
                "Обслуживание не выполнено",
                EventSeverity.ERROR,
                detail,
                operation_id,
            )
        )

    def _set_progress(self, operation_id: str, label: str) -> None:
        self.progress.set(
            ProgressState(
                ProgressChannel.MAINTENANCE,
                operation_id,
                label,
                ProgressMode.INDETERMINATE,
            )
        )
