from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from smart_launcher.comfy.api_client import ComfyApiClient, ComfyApiError


_ACTIVE_STATUSES = frozenset({"pending", "in_progress"})
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


@dataclass(frozen=True, slots=True)
class JobState:
    job_id: str
    status: str
    data: dict[str, Any]


@dataclass(frozen=True, slots=True)
class JobUpdate:
    active: tuple[JobState, ...]
    newly_terminal: tuple[JobState, ...]


class ComfyJobMonitor:
    """Polls the jobs API because browser prompt events target its own WebSocket."""

    def __init__(
        self,
        client: ComfyApiClient,
        *,
        interval: float = 0.5,
        retry_interval: float = 2.0,
    ) -> None:
        if interval <= 0 or retry_interval <= 0:
            raise ValueError("monitor intervals must be positive")
        self._client = client
        self._interval = interval
        self._retry_interval = retry_interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lifecycle_lock = threading.Lock()

    def start(
        self,
        on_update: Callable[[JobUpdate], None],
        *,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("Job monitor is already running.")
            self._stop.clear()
            thread = threading.Thread(
                target=self._run,
                args=(on_update, on_error),
                name="comfy-job-monitor",
                daemon=True,
            )
            self._thread = thread
            try:
                thread.start()
            except Exception:
                self._thread = None
                raise

    def stop(self, timeout: float = 3.5) -> None:
        with self._lifecycle_lock:
            self._stop.set()
            thread = self._thread
            self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def _run(
        self,
        on_update: Callable[[JobUpdate], None],
        on_error: Callable[[str], None] | None,
    ) -> None:
        known_terminal: set[str] | None = None
        error_reported = False
        while not self._stop.is_set():
            try:
                payload = self._client.jobs()
                jobs = self._parse_jobs(payload)
                terminal = {
                    job.job_id: job
                    for job in jobs
                    if job.status in _TERMINAL_STATUSES
                }
                if known_terminal is None:
                    newly_terminal: tuple[JobState, ...] = ()
                    known_terminal = set(terminal)
                else:
                    newly_terminal = tuple(
                        job
                        for job_id, job in terminal.items()
                        if job_id not in known_terminal
                    )
                    known_terminal.update(terminal)
                active = tuple(job for job in jobs if job.status in _ACTIVE_STATUSES)
                on_update(JobUpdate(active, newly_terminal))
                error_reported = False
                self._stop.wait(self._interval)
            except (ComfyApiError, ValueError, TypeError) as exc:
                if on_error is not None and not error_reported:
                    on_error(f"{type(exc).__name__}: {exc}")
                error_reported = True
                self._stop.wait(self._retry_interval)

    @staticmethod
    def _parse_jobs(payload: Mapping[str, Any]) -> tuple[JobState, ...]:
        raw_jobs = payload.get("jobs")
        if not isinstance(raw_jobs, list):
            raise ValueError("ComfyUI jobs response has no jobs list.")
        jobs: list[JobState] = []
        for raw in raw_jobs:
            if not isinstance(raw, Mapping):
                continue
            job_id = raw.get("id")
            status = raw.get("status")
            if not isinstance(job_id, str) or not isinstance(status, str):
                continue
            jobs.append(JobState(job_id, status, dict(raw)))
        return tuple(jobs)
