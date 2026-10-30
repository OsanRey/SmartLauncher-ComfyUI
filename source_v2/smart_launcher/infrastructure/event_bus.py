from __future__ import annotations

import threading
from collections.abc import Callable

from smart_launcher.domain.events import DomainEvent

EventCallback = Callable[[DomainEvent], None]


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subscribers: dict[int, EventCallback] = {}
        self._next_token = 1

    def subscribe(self, callback: EventCallback) -> Callable[[], None]:
        with self._lock:
            token = self._next_token
            self._next_token += 1
            self._subscribers[token] = callback

        def unsubscribe() -> None:
            with self._lock:
                self._subscribers.pop(token, None)

        return unsubscribe

    def publish(self, event: DomainEvent) -> None:
        with self._lock:
            callbacks = tuple(self._subscribers.values())
        for callback in callbacks:
            try:
                callback(event)
            except Exception:
                pass
