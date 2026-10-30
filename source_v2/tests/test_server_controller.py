from __future__ import annotations

import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path

from smart_launcher.application.server_controller import ServerController, ServerLaunch
from smart_launcher.comfy.api_client import ComfyApiClient, ComfyMessageParser
from smart_launcher.comfy.job_monitor import JobState, JobUpdate
from smart_launcher.domain.comfy import ServerState
from smart_launcher.domain.events import ProgressChannel


_FAKE_SERVER = r'''
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

parser = argparse.ArgumentParser()
parser.add_argument("--listen")
parser.add_argument("--port", type=int)
args = parser.parse_args()

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            body = (b'<div id="vue-app"></div>'
                    b'<script type="module" src="/assets/index.js"></script>')
            content_type = "text/html"
        elif self.path == "/assets/index.js":
            body = b"console.log('ready')"
            content_type = "text/javascript"
        elif self.path == "/system_stats":
            body = json.dumps({"system": {"python_version": "test"}}).encode()
            content_type = "application/json"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *_args):
        pass

print("fake comfy booting", flush=True)
print("[WARNING] Test dependency warning\n  pip install demo-package\n", flush=True)
ThreadingHTTPServer((args.listen, args.port), Handler).serve_forever()
'''


class ServerControllerTests(unittest.TestCase):
    def test_process_api_logs_events_and_progress_form_one_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "main.py").write_text(_FAKE_SERVER, encoding="utf-8")
            port = _free_port()
            controller = ServerController(api_client_factory=_ApiOnlyClient)

            session = controller.start(
                ServerLaunch(
                    Path(sys.executable),
                    root,
                    port=port,
                    readiness_timeout=5.0,
                    connect_events=False,
                )
            )
            try:
                self.assertTrue(_wait_for(lambda: controller.state is ServerState.READY, 6.0))
                self.assertIsNone(controller.progress.get(ProgressChannel.STARTUP))
                self.assertIn("fake comfy booting", "".join(r.text for r in controller.logs.snapshot()))
                self.assertIn(
                    "ServerReady",
                    [entry.event.kind for entry in controller.journal.snapshot()],
                )
                self.assertTrue(_wait_for(lambda: any(
                    entry.event.kind == "ConsoleNotice"
                    for entry in controller.journal.snapshot()
                ), 2.0))
                self.assertTrue(session.tree_controlled if sys.platform == "win32" else True)
            finally:
                controller.stop()
            self.assertTrue(_wait_for(lambda: controller.state is ServerState.STOPPED, 3.0))

    def test_early_process_exit_does_not_create_late_readiness_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "main.py").write_text(
                "print('failed early', flush=True); raise SystemExit(3)\n",
                encoding="utf-8",
            )
            controller = ServerController()

            controller.start(
                ServerLaunch(
                    Path(sys.executable),
                    root,
                    port=_free_port(),
                    readiness_timeout=0.5,
                    connect_events=False,
                )
            )

            self.assertTrue(_wait_for(lambda: controller.state is ServerState.FAILED, 3.0))
            time.sleep(0.7)
            kinds = [entry.event.kind for entry in controller.journal.snapshot()]
            self.assertIn("ServerExited", kinds)
            self.assertNotIn("ServerReadinessFailed", kinds)

    def test_websocket_progress_updates_only_generation_channel(self) -> None:
        websocket = FakeWebSocket()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "main.py").write_text(_FAKE_SERVER, encoding="utf-8")
            controller = ServerController(
                websocket_factory=lambda _url: websocket,
                api_client_factory=_ApiOnlyClient,
            )
            controller.start(
                ServerLaunch(
                    Path(sys.executable),
                    root,
                    port=_free_port(),
                    readiness_timeout=5.0,
                )
            )
            try:
                self.assertTrue(_wait_for(lambda: websocket.callback is not None, 6.0))
                websocket.emit(
                    {"type": "progress", "data": {"prompt_id": "p1", "value": 2, "max": 8}}
                )
                current = controller.progress.get(ProgressChannel.GENERATION)
                self.assertIsNotNone(current)
                self.assertEqual(current.operation_id, "p1")  # type: ignore[union-attr]

                websocket.emit(
                    {"type": "execution_success", "data": {"prompt_id": "other"}}
                )
                self.assertIs(controller.progress.get(ProgressChannel.GENERATION), current)
                websocket.emit(
                    {"type": "execution_success", "data": {"prompt_id": "p1"}}
                )
                self.assertIsNone(controller.progress.get(ProgressChannel.GENERATION))
            finally:
                controller.stop()
        self.assertTrue(websocket.stopped)

    def test_browser_jobs_drive_activity_and_terminal_event_without_duplicates(self) -> None:
        controller = ServerController()
        operation_id = "session"
        controller._state = ServerState.READY
        controller._operation_id = operation_id

        controller._on_job_update(
            JobUpdate((JobState("prompt", "in_progress", {}),), ()), operation_id
        )
        progress = controller.progress.get(ProgressChannel.GENERATION)
        self.assertIsNotNone(progress)
        self.assertEqual(progress.mode.value, "indeterminate")  # type: ignore[union-attr]

        completed = JobState("prompt", "completed", {"outputs_count": 1})
        controller._on_job_update(JobUpdate((), (completed,)), operation_id)
        controller._on_comfy_event(
            ComfyMessageParser.parse(
                {"type": "execution_success", "data": {"prompt_id": "prompt"}}
            )
        )

        self.assertIsNone(controller.progress.get(ProgressChannel.GENERATION))
        completions = [
            entry
            for entry in controller.journal.snapshot()
            if entry.event.kind == "Comfy.execution_success"
        ]
        self.assertEqual(len(completions), 1)


class FakeWebSocket:
    def __init__(self) -> None:
        self.callback = None
        self.stopped = False

    def start(self, callback, **_kwargs) -> None:
        self.callback = callback

    def stop(self) -> None:
        self.stopped = True

    def emit(self, payload) -> None:
        assert self.callback is not None
        self.callback(ComfyMessageParser.parse(payload))


class _ApiOnlyClient(ComfyApiClient):
    def frontend(self) -> str:
        return "/assets/index.js"

    def jobs(self, *, limit: int = 200):
        return {"jobs": []}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for(predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.025)
    return predicate()


if __name__ == "__main__":
    unittest.main()
