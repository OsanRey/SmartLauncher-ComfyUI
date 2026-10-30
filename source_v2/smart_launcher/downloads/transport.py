from __future__ import annotations

import http.client
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path

from smart_launcher.domain.downloads import (
    DownloadRequest,
    DownloadResult,
    DownloadSource,
)

ProgressCallback = Callable[[int, int | None], None]


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never forwards credentials from a model host to a signed CDN URL."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and self._origin(req.full_url) != self._origin(newurl):
            redirected.remove_header("Authorization")
            redirected.remove_header("Proxy-Authorization")
        return redirected

    @staticmethod
    def _origin(url: str) -> tuple[str, str, int | None]:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
        if port is None:
            port = 443 if parsed.scheme.casefold() == "https" else 80
        return parsed.scheme.casefold(), (parsed.hostname or "").casefold(), port


class ModelDownloadTransport:
    def __init__(
        self,
        *,
        chunk_size: int = 1024 * 1024,
        timeout: float = 60.0,
        max_retries: int = 8,
        retry_delay: float = 1.0,
        opener: urllib.request.OpenerDirector | None = None,
    ) -> None:
        if chunk_size <= 0 or timeout <= 0 or max_retries < 0 or retry_delay < 0:
            raise ValueError("invalid download transport settings")
        self.chunk_size = chunk_size
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._opener = opener or urllib.request.build_opener(_SafeRedirectHandler())

    def download(
        self,
        request: DownloadRequest,
        *,
        on_progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> DownloadResult:
        destination = Path(os.path.abspath(request.destination))
        try:
            destination.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return DownloadResult(False, request, detail=f"Не удалось создать папку: {exc}")
        target = self._unique_path(destination, request.candidate.name)
        temporary = target.with_name(f".{target.name}.part-{uuid.uuid4().hex[:10]}")
        headers = {
            "User-Agent": "SmartLauncherComfyUI2/2.0",
            "Accept-Encoding": "identity",
        }
        token = (
            request.civitai_token
            if request.candidate.source is DownloadSource.CIVITAI
            else request.huggingface_token
            if request.candidate.source is DownloadSource.HUGGING_FACE
            else None
        )
        if token and token.strip():
            headers["Authorization"] = f"Bearer {token.strip()}"
        written = 0
        total: int | None = None
        retries = 0
        try:
            while True:
                attempt_headers = dict(headers)
                if written:
                    attempt_headers["Range"] = f"bytes={written}-"
                http_request = urllib.request.Request(
                    request.candidate.url, headers=attempt_headers
                )
                try:
                    with self._opener.open(http_request, timeout=self.timeout) as response:
                        status = self._status(response)
                        resumed = written > 0 and status == 206
                        if written and not resumed:
                            temporary.unlink(missing_ok=True)
                            written = 0
                        response_total = self._response_total(
                            response.headers, written if resumed else 0
                        )
                        if response_total is not None:
                            total = response_total
                        mode = "ab" if resumed else ("wb" if temporary.exists() else "xb")
                        with temporary.open(mode) as handle:
                            while True:
                                if cancel_event is not None and cancel_event.is_set():
                                    raise _Cancelled
                                chunk = response.read(self.chunk_size)
                                if not chunk:
                                    break
                                handle.write(chunk)
                                written += len(chunk)
                                # Count only consecutive failures without progress. Large
                                # model downloads may reconnect many times over several hours.
                                retries = 0
                                if on_progress is not None:
                                    on_progress(written, total)
                    break
                except urllib.error.HTTPError as exc:
                    if exc.code not in {408, 429, 500, 502, 503, 504}:
                        raise
                    retries = self._retry_or_raise(retries, cancel_event, exc)
                except (
                    urllib.error.URLError,
                    TimeoutError,
                    ConnectionError,
                    http.client.IncompleteRead,
                ) as exc:
                    if total is not None and written == total:
                        break
                    retries = self._retry_or_raise(retries, cancel_event, exc)
            if total is not None and written != total:
                raise OSError(f"Получено {written} байт вместо ожидаемых {total}.")
            with temporary.open("ab") as handle:
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            return DownloadResult(True, request, target, written, "Модель загружена.")
        except _Cancelled:
            temporary.unlink(missing_ok=True)
            return DownloadResult(
                False, request, bytes_written=written, detail="Загрузка отменена.", cancelled=True
            )
        except urllib.error.HTTPError as exc:
            temporary.unlink(missing_ok=True)
            detail = (
                "Сервер отклонил загрузку. Проверьте токен и доступ к файлу."
                if exc.code in (401, 403)
                else f"Сервер вернул HTTP {exc.code}."
            )
            return DownloadResult(False, request, bytes_written=written, detail=detail)
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            http.client.IncompleteRead,
        ) as exc:
            temporary.unlink(missing_ok=True)
            return DownloadResult(
                False,
                request,
                bytes_written=written,
                detail=f"Не удалось скачать модель: {exc}",
            )

    @staticmethod
    def _unique_path(folder: Path, name: str) -> Path:
        safe = name.replace("\\", "_").replace("/", "_").strip(" .")
        if not safe or safe in (".", ".."):
            safe = "model.bin"
        candidate = folder / safe
        counter = 1
        while candidate.exists():
            candidate = folder / f"{Path(safe).stem} ({counter}){Path(safe).suffix}"
            counter += 1
        return candidate

    @staticmethod
    def _content_length(value: str | None) -> int | None:
        try:
            parsed = int(value) if value is not None else None
        except ValueError:
            return None
        return parsed if parsed is not None and parsed >= 0 else None

    @classmethod
    def _response_total(cls, headers, offset: int) -> int | None:
        content_range = headers.get("Content-Range")
        if content_range and "/" in content_range:
            value = content_range.rsplit("/", 1)[1].strip()
            if value != "*":
                return cls._content_length(value)
        length = cls._content_length(headers.get("Content-Length"))
        return offset + length if length is not None else None

    @staticmethod
    def _status(response) -> int | None:
        status = getattr(response, "status", None)
        if status is not None:
            return int(status)
        getcode = getattr(response, "getcode", None)
        return int(getcode()) if callable(getcode) and getcode() is not None else None

    def _retry_or_raise(
        self,
        retries: int,
        cancel_event: threading.Event | None,
        error: BaseException,
    ) -> int:
        if retries >= self.max_retries:
            raise error
        if cancel_event is not None:
            if cancel_event.wait(self.retry_delay):
                raise _Cancelled
        elif self.retry_delay:
            threading.Event().wait(self.retry_delay)
        return retries + 1


class _Cancelled(Exception):
    pass
