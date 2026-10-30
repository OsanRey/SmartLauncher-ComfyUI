from __future__ import annotations

import threading
import time
import urllib.parse
import uuid
from collections.abc import Callable
from typing import Any, Protocol

from smart_launcher.comfy.api_client import ComfyMessageParser
from smart_launcher.domain.comfy import ComfyEvent, WebSocketState


class WebSocketConnection(Protocol):
    def recv(self, timeout: float | None = None) -> str | bytes: ...

    def close(self) -> None: ...


Connector = Callable[[str, float], WebSocketConnection]
EventCallback = Callable[[ComfyEvent], None]
StateCallback = Callable[[WebSocketState], None]
ErrorCallback = Callable[[str], None]


def _production_connector(url: str, timeout: float) -> WebSocketConnection:
    try:
        from websockets.sync.client import connect
    except ImportError as exc:
        raise RuntimeError(
            "The 'websockets' package is required for ComfyUI live events."
        ) from exc
    return connect(url, open_timeout=timeout, close_timeout=1.0)


class ComfyWebSocketClient:
    """Receives structured ComfyUI events with bounded reconnect backoff."""

    def __init__(
        self,
        base_url: str,
        *,
        client_id: str | None = None,
        connector: Connector = _production_connector,
        connect_timeout: float = 5.0,
        receive_timeout: float = 0.25,
        minimum_backoff: float = 0.25,
        maximum_backoff: float = 5.0,
    ) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("base_url must be an absolute HTTP URL")
        if min(connect_timeout, receive_timeout, minimum_backoff, maximum_backoff) <= 0:
            raise ValueError("WebSocket timeouts and backoff must be positive")
        scheme = "wss" if parsed.scheme == "https" else "ws"
        identifier = client_id or uuid.uuid4().hex
        query = urllib.parse.urlencode({"clientId": identifier})
        self.url = urllib.parse.urlunsplit((scheme, parsed.netloc, "/ws", query, ""))
        self.client_id = identifier
        self._connector = connector
        self._connect_timeout = connect_timeout
        self._receive_timeout = receive_timeout
        self._minimum_backoff = minimum_backoff
        self._maximum_backoff = maximum_backoff
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._connection: WebSocketConnection | None = None
        self._state = WebSocketState.STOPPED

    @property
    def state(self) -> WebSocketState:
        with self._lock:
            return self._state

    def start(
        self,
        on_event: EventCallback,
        *,
        on_state: StateCallback | None = None,
        on_error: ErrorCallback | None = None,
    ) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("WebSocket client is already running.")
            self._stop.clear()
            thread = threading.Thread(
                target=self._run,
                args=(on_event, on_state, on_error),
                name="comfy-websocket",
                daemon=True,
            )
            self._thread = thread
            thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        with self._lock:
            connection = self._connection
            thread = self._thread
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        with self._lock:
            self._thread = None
            self._connection = None
        self._set_state(WebSocketState.STOPPED, None)

    def _run(
        self,
        on_event: EventCallback,
        on_state: StateCallback | None,
        on_error: ErrorCallback | None,
    ) -> None:
        backoff = self._minimum_backoff
        while not self._stop.is_set():
            self._set_state(WebSocketState.CONNECTING, on_state)
            connection: WebSocketConnection | None = None
            try:
                connection = self._connector(self.url, self._connect_timeout)
                with self._lock:
                    self._connection = connection
                self._set_state(WebSocketState.CONNECTED, on_state)
                backoff = self._minimum_backoff
                while not self._stop.is_set():
                    try:
                        message = connection.recv(timeout=self._receive_timeout)
                    except TimeoutError:
                        continue
                    event = ComfyMessageParser.parse(message)
                    try:
                        on_event(event)
                    except Exception:
                        pass
            except Exception as exc:
                if not self._stop.is_set():
                    if on_error is not None:
                        try:
                            on_error(f"{type(exc).__name__}: {exc}")
                        except Exception:
                            pass
                    self._set_state(WebSocketState.RETRYING, on_state)
                    self._stop.wait(backoff)
                    backoff = min(self._maximum_backoff, backoff * 2)
            finally:
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:
                        pass
                with self._lock:
                    if self._connection is connection:
                        self._connection = None
        self._set_state(WebSocketState.STOPPED, on_state)

    def _set_state(
        self, state: WebSocketState, callback: StateCallback | None
    ) -> None:
        with self._lock:
            changed = self._state is not state
            self._state = state
        if changed and callback is not None:
            try:
                callback(state)
            except Exception:
                pass
