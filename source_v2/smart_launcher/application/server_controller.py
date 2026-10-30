from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from smart_launcher.application.console_notices import ConsoleNotices

from smart_launcher.comfy.api_client import (
    ComfyApiClient,
    ComfyApiError,
    ComfyFrontendInterferenceError,
    GenerationTracker,
)
from smart_launcher.comfy.websocket_client import ComfyWebSocketClient
from smart_launcher.comfy.job_monitor import ComfyJobMonitor, JobUpdate
from smart_launcher.domain.commands import CommandSpec
from smart_launcher.domain.comfy import (
    TERMINAL_COMFY_EVENTS,
    ComfyEvent,
    ComfyEventType,
    ServerState,
)
from smart_launcher.domain.events import (
    DomainEvent,
    EventSeverity,
    ProgressChannel,
    ProgressMode,
    ProgressState,
)
from smart_launcher.domain.processes import ProcessExit, ProcessSession
from smart_launcher.infrastructure.event_bus import EventBus
from smart_launcher.infrastructure.process_supervisor import ProcessSupervisor
from smart_launcher.infrastructure.stores import EventJournal, LogStore, ProgressStore


@dataclass(frozen=True, slots=True)
class ServerLaunch:
    python: Path
    root: Path
    host: str = "127.0.0.1"
    port: int = 8188
    arguments: tuple[str, ...] = ()
    readiness_timeout: float = 120.0
    connect_events: bool = True

    def __post_init__(self) -> None:
        if not 1 <= self.port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        if self.readiness_timeout <= 0:
            raise ValueError("readiness_timeout must be positive")

    @property
    def url(self) -> str:
        display_host = "127.0.0.1" if self.host in ("0.0.0.0", "::") else self.host
        return f"http://{display_host}:{self.port}"


class ServerController:
    """Coordinates one server session without deriving state from console text."""

    def __init__(
        self,
        *,
        supervisor: ProcessSupervisor | None = None,
        logs: LogStore | None = None,
        journal: EventJournal | None = None,
        progress: ProgressStore | None = None,
        events: EventBus | None = None,
        websocket_factory: Callable[[str], ComfyWebSocketClient] | None = None,
        api_client_factory: Callable[[str], ComfyApiClient] | None = None,
    ) -> None:
        self.supervisor = supervisor or ProcessSupervisor()
        self.logs = logs or LogStore()
        self.journal = journal or EventJournal()
        self.progress = progress or ProgressStore()
        self.events = events or EventBus()
        self.generation = GenerationTracker()
        self._websocket_factory = websocket_factory or ComfyWebSocketClient
        self._api_client_factory = api_client_factory or ComfyApiClient
        self._lock = threading.RLock()
        self._state = ServerState.STOPPED
        self._operation_id: str | None = None
        self._readiness_cancel: threading.Event | None = None
        self._websocket: ComfyWebSocketClient | None = None
        self._job_monitor: ComfyJobMonitor | None = None
        self._published_terminal_jobs: set[str] = set()

    @property
    def state(self) -> ServerState:
        with self._lock:
            return self._state

    def start(self, launch: ServerLaunch) -> ProcessSession:
        python = launch.python.resolve()
        root = launch.root.resolve()
        main = root / "main.py"
        if not python.is_file():
            raise FileNotFoundError(f"Не найден Python окружения: {python}")
        if not main.is_file():
            raise FileNotFoundError(f"Не найден файл запуска ComfyUI: {main}")
        with self._lock:
            if self._state not in (ServerState.STOPPED, ServerState.FAILED):
                raise RuntimeError(
                    f"ComfyUI нельзя запустить в состоянии {self._state.value}."
                )
            operation_id = uuid.uuid4().hex
            cancel = threading.Event()
            self._operation_id = operation_id
            self._readiness_cancel = cancel
            self._websocket = None
            self._job_monitor = None
            self._published_terminal_jobs.clear()
            self._state = ServerState.STARTING
            self.generation.clear()

        self.logs.session_marker(operation_id, "Сеанс ComfyUI запущен")
        self._publish(
            DomainEvent(
                "ServerStarting",
                "Запуск ComfyUI",
                operation_id=operation_id,
                data={"url": launch.url},
            )
        )
        self.progress.set(
            ProgressState(
                ProgressChannel.STARTUP,
                operation_id,
                "Проверка API и интерфейса ComfyUI",
                ProgressMode.INDETERMINATE,
            )
        )

        spec = CommandSpec(
            argv=(
                str(python),
                str(main),
                "--listen",
                launch.host,
                "--port",
                str(launch.port),
                *launch.arguments,
            ),
            cwd=root,
            env={"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
            source="comfyui",
        )
        notices = ConsoleNotices(self._publish, operation_id)

        def on_chunk(chunk):
            self.logs.append_chunk(chunk, session_id=operation_id, operation_id=operation_id)
            notices.feed(chunk.stream.value, chunk.text)

        def on_exit(event):
            notices.flush()
            self._on_exit(event, operation_id)

        try:
            session = self.supervisor.start(
                spec,
                on_chunk=on_chunk,
                on_exit=on_exit,
            )
        except Exception:
            with self._lock:
                self._state = ServerState.FAILED
            self.progress.clear(ProgressChannel.STARTUP, operation_id)
            raise

        threading.Thread(
            target=self._wait_ready,
            args=(launch, operation_id, cancel),
            name=f"comfy-readiness-{session.pid}",
            daemon=True,
        ).start()
        return session

    def stop(self) -> bool:
        with self._lock:
            if self._state is ServerState.STOPPED:
                return False
            self._state = ServerState.STOPPING
            if self._readiness_cancel is not None:
                self._readiness_cancel.set()
            websocket = self._websocket
            self._websocket = None
            job_monitor = self._job_monitor
            self._job_monitor = None
        if websocket is not None:
            websocket.stop()
        if job_monitor is not None:
            job_monitor.stop()
        return self.supervisor.stop()

    def _wait_ready(
        self,
        launch: ServerLaunch,
        operation_id: str,
        cancel: threading.Event,
    ) -> None:
        client = self._api_client_factory(launch.url)
        try:
            stats = client.wait_ready(
                launch.readiness_timeout,
                cancel_event=cancel,
            )
        except ComfyApiError as exc:
            with self._lock:
                if self._operation_id != operation_id or self._state is not ServerState.STARTING:
                    return
                self._state = ServerState.FAILED
            self.progress.clear(ProgressChannel.STARTUP, operation_id)
            self._publish(
                DomainEvent(
                    (
                        "FrontendInterferenceDetected"
                        if isinstance(exc, ComfyFrontendInterferenceError)
                        else "ServerReadinessFailed"
                    ),
                    (
                        "Локальный интерфейс ComfyUI перехвачен"
                        if isinstance(exc, ComfyFrontendInterferenceError)
                        else "API или интерфейс ComfyUI не готовы"
                    ),
                    EventSeverity.ERROR,
                    str(exc),
                    operation_id,
                )
            )
            self.supervisor.stop()
            return

        with self._lock:
            if self._operation_id != operation_id or self._state is not ServerState.STARTING:
                return
            self._state = ServerState.READY
        self.progress.clear(ProgressChannel.STARTUP, operation_id)
        self._publish(
            DomainEvent(
                "ServerReady",
                "ComfyUI готов к работе",
                EventSeverity.SUCCESS,
                operation_id=operation_id,
                data={"url": launch.url, "system_stats": stats},
            )
        )
        if launch.connect_events:
            websocket = self._websocket_factory(launch.url)
            job_monitor = ComfyJobMonitor(client)
            with self._lock:
                if self._operation_id != operation_id or self._state is not ServerState.READY:
                    return
                self._websocket = websocket
                self._job_monitor = job_monitor
            websocket.start(
                self._on_comfy_event,
                on_error=lambda detail: self._on_websocket_error(detail, operation_id),
            )
            job_monitor.start(
                lambda update: self._on_job_update(update, operation_id),
                on_error=lambda detail: self._on_job_monitor_error(detail, operation_id),
            )

    def _on_exit(self, exit_event: ProcessExit, operation_id: str) -> None:
        with self._lock:
            if self._operation_id != operation_id:
                return
            if self._readiness_cancel is not None:
                self._readiness_cancel.set()
            expected = exit_event.stop_requested or self._state is ServerState.STOPPING
            self._state = ServerState.STOPPED if expected else ServerState.FAILED
            self._readiness_cancel = None
            websocket = self._websocket
            self._websocket = None
            job_monitor = self._job_monitor
            self._job_monitor = None
        if websocket is not None:
            websocket.stop()
        if job_monitor is not None:
            job_monitor.stop()
        self.generation.clear()
        current_generation = self.progress.get(ProgressChannel.GENERATION)
        if current_generation is not None:
            self.progress.clear(
                ProgressChannel.GENERATION, current_generation.operation_id
            )
        self.progress.clear(ProgressChannel.STARTUP, operation_id)
        self._publish(
            DomainEvent(
                "ServerStopped" if expected else "ServerExited",
                "ComfyUI остановлен" if expected else "Процесс ComfyUI завершился",
                EventSeverity.INFO if expected else EventSeverity.ERROR,
                f"Код завершения: {exit_event.return_code}",
                operation_id,
            )
        )

    def _on_comfy_event(self, event: ComfyEvent) -> None:
        self.generation.apply(event)
        if event.type is ComfyEventType.PROGRESS and event.prompt_id is not None:
            try:
                value = float(event.data["value"])
                maximum = float(event.data["max"])
            except (KeyError, TypeError, ValueError):
                return
            node = event.data.get("node")
            label = "Генерация" if node is None else f"Генерация: {node}"
            self.progress.set(
                ProgressState(
                    ProgressChannel.GENERATION,
                    event.prompt_id,
                    label,
                    ProgressMode.DETERMINATE,
                    value,
                    maximum,
                )
            )
            return
        if event.type in TERMINAL_COMFY_EVENTS and event.prompt_id is not None:
            self.progress.clear(ProgressChannel.GENERATION, event.prompt_id)
            if not self._claim_terminal_job(event.prompt_id):
                return
            severity = (
                EventSeverity.SUCCESS
                if event.type is ComfyEventType.EXECUTION_SUCCESS
                else EventSeverity.WARNING
                if event.type is ComfyEventType.EXECUTION_INTERRUPTED
                else EventSeverity.ERROR
            )
            titles = {
                ComfyEventType.EXECUTION_SUCCESS: "Генерация завершена",
                ComfyEventType.EXECUTION_INTERRUPTED: "Генерация отменена",
                ComfyEventType.EXECUTION_ERROR: "Ошибка генерации",
            }
            self._publish(
                DomainEvent(
                    f"Comfy.{event.type.value}",
                    titles.get(event.type, "Задание ComfyUI завершено"),
                    severity,
                    operation_id=event.prompt_id,
                    data=event.data,
                )
            )

    def _on_websocket_error(self, detail: str, operation_id: str) -> None:
        with self._lock:
            if self._operation_id != operation_id or self._state is not ServerState.READY:
                return
        self._publish(
            DomainEvent(
                "WebSocketRetrying",
                "Переподключение к событиям ComfyUI",
                EventSeverity.WARNING,
                detail,
                operation_id,
            )
        )

    def _on_job_update(self, update: JobUpdate, operation_id: str) -> None:
        with self._lock:
            if self._operation_id != operation_id or self._state is not ServerState.READY:
                return
        monitor_progress_id = f"{operation_id}:browser-jobs"
        current = self.progress.get(ProgressChannel.GENERATION)
        if update.active:
            if current is None or current.operation_id == monitor_progress_id:
                running = sum(job.status == "in_progress" for job in update.active)
                pending = len(update.active) - running
                if running:
                    label = "Генерация выполняется"
                else:
                    label = "Задание ожидает запуска"
                if pending and running:
                    label += f" · в очереди: {pending}"
                self.progress.set(
                    ProgressState(
                        ProgressChannel.GENERATION,
                        monitor_progress_id,
                        label,
                        ProgressMode.INDETERMINATE,
                    )
                )
        elif current is not None and current.operation_id == monitor_progress_id:
            self.progress.clear(ProgressChannel.GENERATION, monitor_progress_id)

        terminal = {
            "completed": ("Comfy.execution_success", "Генерация завершена", EventSeverity.SUCCESS),
            "failed": ("Comfy.execution_error", "Ошибка генерации", EventSeverity.ERROR),
            "cancelled": (
                "Comfy.execution_interrupted",
                "Генерация отменена",
                EventSeverity.WARNING,
            ),
        }
        for job in update.newly_terminal:
            if not self._claim_terminal_job(job.job_id):
                continue
            kind, title, severity = terminal[job.status]
            self._publish(
                DomainEvent(
                    kind,
                    title,
                    severity,
                    operation_id=job.job_id,
                    data=job.data,
                )
            )

    def _on_job_monitor_error(self, detail: str, operation_id: str) -> None:
        with self._lock:
            if self._operation_id != operation_id or self._state is not ServerState.READY:
                return
        self._publish(
            DomainEvent(
                "JobMonitorRetrying",
                "Наблюдение за заданиями ComfyUI переподключается",
                EventSeverity.WARNING,
                detail,
                operation_id,
            )
        )

    def _claim_terminal_job(self, job_id: str) -> bool:
        with self._lock:
            if job_id in self._published_terminal_jobs:
                return False
            self._published_terminal_jobs.add(job_id)
            return True

    def _publish(self, event: DomainEvent) -> None:
        self.journal.append(event)
        self.events.publish(event)
