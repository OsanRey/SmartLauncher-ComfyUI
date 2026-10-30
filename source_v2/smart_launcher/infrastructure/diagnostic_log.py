from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import threading
from collections.abc import Iterable

from smart_launcher.domain.events import JournalEntry, LogRecord


_BEARER = re.compile(r"(?i)(\b(?:authorization\s*[:=]\s*)?bearer\s+)[^\s,;]+")
_SECRET_FIELD = re.compile(
    r"(?i)(\b(?:token|api[_-]?key|access[_-]?token|authorization)\s*[:=]\s*)"
    r"([^\s,;&]+)"
)
_SECRET_QUERY = re.compile(
    r"(?i)([?&](?:token|api[_-]?key|access[_-]?token|authorization)=)[^&#\s]+"
)
_TERMINAL_PROGRESS = re.compile(r"^\s*(?:\[PROGRESS\]\s*)?\d{1,3}%\s*")


class DiagnosticLog:
    """Small rotating diagnostic journal that must never affect launcher work."""

    def __init__(
        self,
        directory: Path | str,
        *,
        max_bytes: int = 1_000_000,
        backup_count: int = 3,
        secrets: Iterable[str | None] = (),
    ) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "launcher.log"
        self._lock = threading.RLock()
        self._closed = False
        self._secrets: tuple[str, ...] = ()
        self.set_secrets(secrets)
        self._logger = logging.getLogger(f"smart_launcher.diagnostics.{id(self)}")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        handler = RotatingFileHandler(
            self.path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
            delay=True,
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        self._logger.addHandler(handler)
        self._handler = handler
        self._write("launcher", "internal", "Smart Launcher 2.0 session started")

    def set_secrets(self, secrets: Iterable[str | None]) -> None:
        with self._lock:
            self._secrets = tuple(
                sorted(
                    {value for value in secrets if value and len(value) >= 4},
                    key=len,
                    reverse=True,
                )
            )

    def append_log(self, record: LogRecord) -> None:
        if record.transient or self._closed:
            return
        self._write(
            record.source,
            record.stream,
            record.text,
            timestamp=record.timestamp.isoformat(timespec="seconds"),
        )

    def append_event(self, entry: JournalEntry) -> None:
        if self._closed:
            return
        event = entry.event
        text = event.title if not event.detail else f"{event.title}: {event.detail}"
        self._write(
            "event",
            event.severity.value,
            text,
            timestamp=event.timestamp.isoformat(timespec="seconds"),
        )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._write("launcher", "internal", "Smart Launcher 2.0 session ended")
            self._closed = True
            self._logger.removeHandler(self._handler)
            self._handler.close()

    def _write(
        self,
        source: str,
        stream: str,
        text: str,
        *,
        timestamp: str | None = None,
    ) -> None:
        cleaned = self._redact(text).replace("\r", "\n")
        lines = cleaned.splitlines() or [""]
        if source == "comfyui":
            lines = [line for line in lines if not _TERMINAL_PROGRESS.match(line)]
            if not lines:
                return
        stamp = timestamp or "session"
        with self._lock:
            for line in lines:
                self._logger.info("[%s] [%s/%s] %s", stamp, source, stream, line)

    def _redact(self, text: str) -> str:
        result = _BEARER.sub(r"\1<redacted>", text)
        result = _SECRET_QUERY.sub(r"\1<redacted>", result)
        result = _SECRET_FIELD.sub(r"\1<redacted>", result)
        for secret in self._secrets:
            result = result.replace(secret, "<redacted>")
        return result
