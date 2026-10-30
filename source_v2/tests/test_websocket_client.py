from __future__ import annotations

import threading
import time
import unittest

from smart_launcher.comfy.websocket_client import ComfyWebSocketClient
from smart_launcher.domain.comfy import ComfyEventType, WebSocketState


class FakeConnection:
    def __init__(self, messages: list[str]) -> None:
        self.messages = list(messages)
        self.closed = False

    def recv(self, timeout=None):
        if self.closed:
            raise OSError("closed")
        if self.messages:
            return self.messages.pop(0)
        time.sleep(min(timeout or 0.01, 0.01))
        raise TimeoutError

    def close(self) -> None:
        self.closed = True


class WebSocketClientTests(unittest.TestCase):
    def test_receives_structured_events_and_stops_cleanly(self) -> None:
        connection = FakeConnection(
            ['{"type":"progress","data":{"prompt_id":"p","value":1,"max":4}}']
        )
        received = []
        ready = threading.Event()
        client = ComfyWebSocketClient(
            "http://127.0.0.1:8188",
            connector=lambda _url, _timeout: connection,
            receive_timeout=0.02,
        )
        client.start(
            lambda event: (received.append(event), ready.set()),
        )
        try:
            self.assertTrue(ready.wait(2.0))
            self.assertEqual(received[0].type, ComfyEventType.PROGRESS)
            self.assertIn("clientId=", client.url)
        finally:
            client.stop()
        self.assertEqual(client.state, WebSocketState.STOPPED)
        self.assertTrue(connection.closed)

    def test_reconnects_after_connector_failure(self) -> None:
        calls = 0
        connected = threading.Event()
        states = []

        def connector(_url: str, _timeout: float):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("offline")
            connected.set()
            return FakeConnection([])

        client = ComfyWebSocketClient(
            "http://127.0.0.1:8188",
            connector=connector,
            minimum_backoff=0.01,
            maximum_backoff=0.02,
            receive_timeout=0.01,
        )
        client.start(lambda _event: None, on_state=states.append)
        try:
            self.assertTrue(connected.wait(2.0))
        finally:
            client.stop()

        self.assertGreaterEqual(calls, 2)
        self.assertIn(WebSocketState.RETRYING, states)
        self.assertIn(WebSocketState.CONNECTED, states)


if __name__ == "__main__":
    unittest.main()
