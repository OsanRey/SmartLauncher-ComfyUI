from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from smart_launcher.application.installation_pipeline import InstallationPipeline
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
from smart_launcher.domain.installation import InstallationRequest, InstallationResult
from smart_launcher.domain.runtimes import RuntimeProbe
from smart_launcher.infrastructure.stores import EventJournal, LogStore, ProgressStore
from smart_launcher.runtime.python_resolver import PythonResolver

InstallSuccessCallback = Callable[[InstallationResult, RuntimeProbe, "InstallationJob"], None]


@dataclass(frozen=True, slots=True)
class InstallationJob:
    request: InstallationRequest
    base_python: Path | None = None
    torch_packages: tuple[str, ...] = ("torch", "torchvision", "torchaudio")
    torch_index_url: str | None = "https://download.pytorch.org/whl/cu130"
    torch_expectation: TorchExpectation = TorchExpectation()


class InstallationController:
    def __init__(
        self,
        pipeline: InstallationPipeline,
        resolver: PythonResolver,
        logs: LogStore,
        journal: EventJournal,
        progress: ProgressStore,
        *,
        on_success: InstallSuccessCallback | None = None,
        coordinator: OperationCoordinator | None = None,
    ) -> None:
        self.pipeline = pipeline
        self.resolver = resolver
        self.logs = logs
        self.journal = journal
        self.progress = progress
        self.on_success = on_success
        self.coordinator = coordinator or OperationCoordinator()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._operation_id: str | None = None
        self._last_result: InstallationResult | None = None

    @property
    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    @property
    def last_result(self) -> InstallationResult | None:
        with self._lock:
            return self._last_result

    def start(self, job: InstallationJob) -> bool:
        lease = self.coordinator.try_acquire(OperationKind.INSTALLATION)
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
                args=(job, operation_id, self._cancel, lease),
                name="launcher-installation",
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
            self._set_progress(operation_id, "Отмена установки...")
        return True

    def close(self, timeout: float = 5.0) -> None:
        self.cancel()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def _guarded_run(
        self,
        job: InstallationJob,
        operation_id: str,
        cancel: threading.Event,
        lease: OperationLease,
    ) -> None:
        try:
            self._run(job, operation_id, cancel)
        except Exception as exc:
            self._publish_failure(operation_id, f"{type(exc).__name__}: {exc}")
        finally:
            self.progress.clear(ProgressChannel.MAINTENANCE, operation_id)
            with self._lock:
                if self._operation_id == operation_id:
                    self._thread = None
            lease.release()

    def _run(
        self,
        job: InstallationJob,
        operation_id: str,
        cancel: threading.Event,
    ) -> None:
        self.journal.append(
            DomainEvent(
                "InstallationStarted",
                "Начата первая установка ComfyUI",
                operation_id=operation_id,
            )
        )
        self._set_progress(operation_id, "Поиск подходящего Python...")
        configured = (job.base_python,) if job.base_python is not None else ()
        resolution = self.resolver.resolve(configured)
        if resolution.selected is None:
            detail = "; ".join(resolution.diagnostics) or "Подходящий Python не найден."
            self._publish_failure(operation_id, detail)
            return

        def on_chunk(chunk) -> None:
            self.logs.append_chunk(chunk, operation_id=operation_id)
            labels = {
                "git-clone": "Загрузка ядра ComfyUI...",
                "venv-create": "Создание изолированного окружения...",
                "pip-torch": "Установка профиля PyTorch...",
                "pip-requirements": "Установка зависимостей ComfyUI...",
                "pip-check": "Финальная проверка пакетов...",
            }
            label = labels.get(chunk.source)
            if label:
                self._set_progress(operation_id, label)

        self._set_progress(operation_id, "Подготовка установки...")
        result = self.pipeline.execute(
            job.request,
            resolution.selected,
            torch_packages=job.torch_packages,
            torch_index_url=job.torch_index_url,
            torch_expectation=job.torch_expectation,
            on_chunk=on_chunk,
            cancel_event=cancel,
        )
        with self._lock:
            self._last_result = result
        if not result.succeeded:
            self._publish_failure(operation_id, result.detail)
            return
        if self.on_success is not None:
            self.on_success(result, resolution.selected, job)
        self.journal.append(
            DomainEvent(
                "InstallationCompleted",
                "ComfyUI установлен и готов",
                EventSeverity.SUCCESS,
                result.detail,
                operation_id,
                data={"target": str(result.target), "commit": result.commit or ""},
            )
        )

    def _publish_failure(self, operation_id: str, detail: str) -> None:
        self.journal.append(
            DomainEvent(
                "InstallationFailed",
                "Установка ComfyUI не выполнена",
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
