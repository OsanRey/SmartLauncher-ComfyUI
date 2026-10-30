from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from smart_launcher.comfy.api_client import (
    ComfyApiClient,
    ComfyApiError,
    ComfyFrontendInterferenceError,
    ComfyMessageParser,
    GenerationTracker,
)
from smart_launcher.domain.comfy import ComfyEventType


class ApiHandler(BaseHTTPRequestHandler):
    posts: list[tuple[str, dict[str, object]]] = []
    frontend_mode = "ready"

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/":
            if self.frontend_mode == "intercepted":
                self._body(
                    b"<html><title>AdGuard</title>blocking-pages local.adguard.org</html>",
                    "text/html",
                    status=502,
                )
            elif self.frontend_mode == "incomplete":
                self._body(b"<html><body>Loading</body></html>", "text/html")
            else:
                self._body(
                    b'<html><body><div id="vue-app"></div>'
                    b'<script type="module" src="./assets/index.js"></script></body></html>',
                    "text/html",
                )
            return
        if self.path == "/assets/index.js":
            self._body(b"console.log('ready')", "text/javascript")
            return
        payload = {
            "/system_stats": {"system": {"python_version": "3.13.15"}},
            "/queue": {"queue_running": [], "queue_pending": []},
            "/features": {"supports_preview_metadata": True},
            "/api/jobs?limit=25": {"jobs": [], "pagination": {"total": 0}},
        }.get(self.path)
        if payload is None:
            self.send_error(404)
            return
        self._json(payload)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b"{}"
        payload = json.loads(body)
        self.posts.append((self.path, payload))
        self._json({})

    def log_message(self, _format: str, *_args: object) -> None:
        pass

    def _json(self, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self._body(body, "application/json")

    def _body(self, body: bytes, content_type: str, *, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ApiClientTests(unittest.TestCase):
    def setUp(self) -> None:
        ApiHandler.posts.clear()
        ApiHandler.frontend_mode = "ready"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), ApiHandler)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        host, port = self.server.server_address
        self.client = ComfyApiClient(f"http://{host}:{port}")

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(2.0)

    def test_readiness_uses_system_stats(self) -> None:
        with patch.object(
            self.client, "frontend", return_value="/assets/index.js"
        ) as frontend:
            stats = self.client.wait_ready(2.0)

        self.assertEqual(stats["system"]["python_version"], "3.13.15")
        self.assertEqual(frontend.call_count, 2)

    def test_frontend_probe_checks_entry_module(self) -> None:
        responses = [
            (
                b'<html><div id="vue-app"></div>'
                b'<script type="module" src="./assets/index.js"></script></html>',
                "text/html",
            ),
            (b"console.log('ready')", "text/javascript"),
        ]
        with patch.object(self.client, "_request_bytes", side_effect=responses) as request:
            entry = self.client.frontend()

        self.assertEqual(entry, "/assets/index.js")
        self.assertEqual(request.call_args_list[1].args[:2], ("GET", "/assets/index.js"))

    def test_frontend_probe_requires_vue_mount_and_module(self) -> None:
        with patch.object(
            self.client,
            "_request_bytes",
            return_value=(b"<html><body>Loading</body></html>", "text/html"),
        ):
            with self.assertRaisesRegex(ComfyApiError, "#vue-app"):
                self.client.frontend()

    def test_frontend_probe_reports_adguard_interception(self) -> None:
        with patch.object(
            self.client,
            "_request_bytes",
            return_value=(
                b"<html>blocking-pages local.adguard.org</html>",
                "text/html",
            ),
        ):
            with self.assertRaisesRegex(ComfyFrontendInterferenceError, "AdGuard"):
                self.client.frontend()

    def test_commands_use_structured_http_endpoints(self) -> None:
        self.client.interrupt()
        self.client.free(unload_models=True, free_memory=False)

        self.assertEqual(ApiHandler.posts[0], ("/interrupt", {}))
        self.assertEqual(
            ApiHandler.posts[1],
            ("/free", {"unload_models": True, "free_memory": False}),
        )

    def test_jobs_uses_structured_jobs_endpoint(self) -> None:
        result = self.client.jobs(limit=25)

        self.assertEqual(result["jobs"], [])

    def test_generation_progress_is_cleared_only_by_matching_terminal_event(self) -> None:
        tracker = GenerationTracker()
        tracker.apply(
            ComfyMessageParser.parse(
                {"type": "progress", "data": {"prompt_id": "a", "value": 3, "max": 10}}
            )
        )
        tracker.apply(
            ComfyMessageParser.parse(
                {"type": "progress", "data": {"prompt_id": "b", "value": 7, "max": 10}}
            )
        )
        tracker.apply(
            ComfyMessageParser.parse(
                {"type": "execution_success", "data": {"prompt_id": "a"}}
            )
        )

        self.assertEqual([progress.prompt_id for progress in tracker.active], ["b"])

    def test_unknown_message_type_is_preserved(self) -> None:
        event = ComfyMessageParser.parse({"type": "future_event", "data": {"value": 1}})

        self.assertEqual(event.type, ComfyEventType.UNKNOWN)
        self.assertEqual(event.raw_type, "future_event")


if __name__ == "__main__":
    unittest.main()
