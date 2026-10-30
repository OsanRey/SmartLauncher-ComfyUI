from __future__ import annotations

import re
import threading
from collections import deque

from smart_launcher.domain.events import DomainEvent, EventSeverity
from smart_launcher.infrastructure.stores import strip_terminal_control_sequences


class ConsoleNotices:
    """Extract advisory log blocks, without executing commands or setting server state."""

    _level = re.compile(r"^\s*\[(?:INFO|DEBUG|WARNING|WARN|ERROR|CRITICAL)\]")
    _warning = re.compile(r"^\s*\[(?:WARNING|WARN|ERROR|CRITICAL)\]")
    _action = re.compile(r"\bpip(?:3)?\s+install\b|\bModuleNotFoundError\b|\bIMPORT FAILED\b", re.I)
    _dependency_failure = re.compile(
        r"\bModule\s+['\"]([^'\"]+)['\"]\s+load failed\b", re.I
    )

    def __init__(self, publish, operation_id: str):
        self._publish = publish
        self._operation_id = operation_id
        self._buffers: dict[str, str] = {}
        self._blocks: dict[str, list[str]] = {}
        self._context: dict[str, deque[str]] = {}
        self._seen: set[str] = set()
        self._lock = threading.RLock()

    def feed(self, stream: str, text: str) -> None:
        with self._lock:
            text = self._buffers.get(stream, "") + text
            lines = text.split("\n")
            self._buffers[stream] = lines.pop()[-16384:]
            for line in lines:
                self._line(stream, strip_terminal_control_sequences(line).rstrip("\r"))

    def flush(self) -> None:
        with self._lock:
            for stream, text in self._buffers.items():
                if text:
                    self._line(stream, strip_terminal_control_sequences(text))
            self._buffers.clear()
            for stream in list(self._blocks):
                self._emit(stream)

    def _line(self, stream: str, line: str) -> None:
        context = self._context.setdefault(stream, deque(maxlen=3))
        if not line.strip() or self._level.match(line):
            self._emit(stream)
            context.clear()
        if self._warning.match(line):
            self._blocks[stream] = [line]
        elif self._blocks.get(stream):
            self._blocks[stream].append(line)
            if len(self._blocks[stream]) >= 12 or self._action.search(line):
                self._emit(stream)
        elif self._dependency_failure.search(line):
            self._blocks[stream] = [*context, line]
            context.clear()
        elif self._action.search(line):
            self._blocks[stream] = [*context, line]
            context.clear()
            self._emit(stream)
        elif line.strip():
            context.append(line[:2000])

    def _emit(self, stream: str) -> None:
        lines = self._blocks.pop(stream, [])
        if lines:
            self._context.get(stream, []).clear()
        detail = "\n".join(lines).strip()[:6000]
        if not detail or detail in self._seen or len(self._seen) >= 100:
            return
        self._seen.add(detail)
        title = "Предупреждение из консоли ComfyUI"
        if "matrix-nio" in detail and "not installed" in detail:
            title = "Обмен через Matrix недоступен (необязательно)"
            detail = "ComfyUI-Manager отключил обмен через Matrix. Для генерации установка matrix-nio не требуется.\n\n" + detail
        elif match := self._dependency_failure.search(detail):
            title = f"Не удалось загрузить модуль {match.group(1)}"
        elif self._action.search(detail):
            title = "Зависимости: сообщение из консоли"
        self._publish(DomainEvent(
            "ConsoleNotice", title, EventSeverity.WARNING, detail,
            self._operation_id,
        ))
