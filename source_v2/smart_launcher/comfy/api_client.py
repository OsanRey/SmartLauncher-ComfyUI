from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from html.parser import HTMLParser
from typing import Any

from smart_launcher.domain.comfy import (
    TERMINAL_COMFY_EVENTS,
    ComfyEvent,
    ComfyEventType,
    GenerationProgress,
)


class ComfyApiError(RuntimeError):
    pass


class ComfyFrontendInterferenceError(ComfyApiError):
    """Raised when another local program replaces ComfyUI frontend responses."""


class _FrontendDocumentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.has_vue_app = False
        self.module_sources: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = dict(attrs)
        if tag == "div" and attributes.get("id") == "vue-app":
            self.has_vue_app = True
        if (
            tag == "script"
            and attributes.get("type", "").casefold() == "module"
            and attributes.get("src")
        ):
            self.module_sources.append(str(attributes["src"]))


class ComfyApiClient:
    def __init__(self, base_url: str, *, request_timeout: float = 3.0) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("base_url must be an absolute HTTP URL")
        self.base_url = base_url.rstrip("/")
        self.request_timeout = request_timeout

    def system_stats(self) -> dict[str, Any]:
        return self._request_json("GET", "/system_stats")

    def queue(self) -> dict[str, Any]:
        return self._request_json("GET", "/queue")

    def history(self, prompt_id: str | None = None) -> dict[str, Any]:
        path = "/history"
        if prompt_id:
            path += "/" + urllib.parse.quote(prompt_id, safe="")
        return self._request_json("GET", path)

    def features(self) -> dict[str, Any]:
        return self._request_json("GET", "/features")

    def jobs(self, *, limit: int = 200) -> dict[str, Any]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        return self._request_json("GET", f"/api/jobs?limit={limit}")

    def frontend(self) -> str:
        body, content_type = self._request_bytes(
            "GET", "/", accept="text/html,application/xhtml+xml"
        )
        self._raise_if_intercepted(body, "/")
        if "html" not in content_type.casefold():
            raise ComfyApiError(
                f"GET / returned {content_type or 'an unknown content type'}, not HTML."
            )
        try:
            document = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ComfyApiError(f"GET / returned invalid UTF-8 HTML: {exc}") from exc

        parser = _FrontendDocumentParser()
        parser.feed(document)
        if not parser.has_vue_app:
            raise ComfyApiError("ComfyUI frontend HTML does not contain #vue-app.")
        if not parser.module_sources:
            raise ComfyApiError("ComfyUI frontend HTML has no module entry point.")

        entry_url = urllib.parse.urljoin(self.base_url + "/", parser.module_sources[0])
        if urllib.parse.urlsplit(entry_url).netloc != urllib.parse.urlsplit(
            self.base_url
        ).netloc:
            raise ComfyApiError("ComfyUI frontend entry point points to another host.")
        entry_path = urllib.parse.urlsplit(entry_url).path
        entry_body, entry_type = self._request_bytes(
            "GET", entry_path, accept="text/javascript,application/javascript,*/*"
        )
        self._raise_if_intercepted(entry_body, entry_path)
        if not entry_body:
            raise ComfyApiError(f"GET {entry_path} returned an empty frontend module.")
        if "html" in entry_type.casefold():
            raise ComfyApiError(
                f"GET {entry_path} returned HTML instead of a JavaScript module."
            )
        return entry_path

    def interrupt(self) -> dict[str, Any]:
        return self._request_json("POST", "/interrupt", payload={})

    def free(self, *, unload_models: bool = True, free_memory: bool = True) -> dict[str, Any]:
        return self._request_json(
            "POST",
            "/free",
            payload={"unload_models": unload_models, "free_memory": free_memory},
        )

    def wait_ready(
        self,
        timeout: float,
        *,
        interval: float = 0.25,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        if timeout <= 0 or interval <= 0:
            raise ValueError("timeout and interval must be positive")
        deadline = time.monotonic() + timeout
        last_error = "Server did not respond."
        clean_frontend_checks = 0
        stats: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise ComfyApiError("Readiness check was cancelled.")
            try:
                stats = self.system_stats()
                self.frontend()
                clean_frontend_checks += 1
                if clean_frontend_checks >= 2:
                    return stats
            except ComfyFrontendInterferenceError:
                raise
            except ComfyApiError as exc:
                last_error = str(exc)
                clean_frontend_checks = 0
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(interval, remaining))
        raise ComfyApiError(f"ComfyUI did not become ready: {last_error}")

    @staticmethod
    def _raise_if_intercepted(body: bytes, path: str) -> None:
        sample = body[:512_000].lower()
        markers = (b"local.adguard.org", b"blocking-pages", b"adguard assistant")
        if any(marker in sample for marker in markers):
            raise ComfyFrontendInterferenceError(
                "AdGuard подменяет локальный ответ ComfyUI "
                f"({path}). Добавьте 127.0.0.1 и localhost в исключения фильтрации."
            )

    def _request_bytes(
        self,
        method: str,
        path: str,
        *,
        accept: str,
        payload: bytes | None = None,
        content_type: str | None = None,
    ) -> tuple[bytes, str]:
        headers = {"Accept": accept}
        if content_type is not None:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(
            self.base_url + path,
            data=payload,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.request_timeout) as response:
                return response.read(), response.headers.get_content_type()
        except urllib.error.HTTPError as exc:
            error_body = exc.read()
            self._raise_if_intercepted(error_body, path)
            raise ComfyApiError(f"{method} {path} failed: HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ComfyApiError(f"{method} {path} failed: {exc}") from exc

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(dict(payload)).encode("utf-8")
            headers["Content-Type"] = "application/json"
        body, _content_type = self._request_bytes(
            method,
            path,
            accept=headers["Accept"],
            payload=data,
            content_type=headers.get("Content-Type"),
        )
        if not body:
            return {}
        try:
            result = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ComfyApiError(f"{method} {path} returned invalid JSON: {exc}") from exc
        if not isinstance(result, dict):
            raise ComfyApiError(f"{method} {path} returned a non-object JSON value.")
        return result


class ComfyMessageParser:
    @staticmethod
    def parse(message: str | bytes | Mapping[str, Any]) -> ComfyEvent:
        if isinstance(message, bytes):
            message = message.decode("utf-8")
        if isinstance(message, str):
            payload = json.loads(message)
        else:
            payload = dict(message)
        if not isinstance(payload, dict):
            raise ValueError("ComfyUI message must be a JSON object.")
        raw_type = str(payload.get("type") or "")
        try:
            event_type = ComfyEventType(raw_type)
        except ValueError:
            event_type = ComfyEventType.UNKNOWN
        raw_data = payload.get("data")
        data = dict(raw_data) if isinstance(raw_data, Mapping) else {}
        prompt_id = data.get("prompt_id")
        return ComfyEvent(
            event_type,
            str(prompt_id) if prompt_id is not None else None,
            data,
            raw_type,
        )


class GenerationTracker:
    """Tracks independent prompt progress from structured ComfyUI events."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._active: dict[str, GenerationProgress] = {}

    @property
    def active(self) -> tuple[GenerationProgress, ...]:
        with self._lock:
            return tuple(self._active.values())

    def apply(self, event: ComfyEvent) -> None:
        prompt_id = event.prompt_id
        if prompt_id is None:
            return
        if event.type in TERMINAL_COMFY_EVENTS:
            with self._lock:
                self._active.pop(prompt_id, None)
            return
        if event.type is not ComfyEventType.PROGRESS:
            return
        try:
            value = int(event.data["value"])
            maximum = int(event.data["max"])
        except (KeyError, TypeError, ValueError):
            return
        node = event.data.get("node")
        with self._lock:
            self._active[prompt_id] = GenerationProgress(
                prompt_id,
                str(node) if node is not None else None,
                value,
                maximum,
            )

    def clear(self) -> None:
        with self._lock:
            self._active.clear()
