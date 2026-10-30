from __future__ import annotations

import tempfile
import threading
import time
import unittest
import unittest.mock
import urllib.request
from pathlib import Path

from smart_launcher.application.download_controller import DownloadController
from smart_launcher.domain.downloads import (
    DownloadCandidate,
    DownloadRequest,
    DownloadResult,
    DownloadSource,
)
from smart_launcher.domain.events import ProgressChannel
from smart_launcher.downloads.transport import ModelDownloadTransport
from smart_launcher.downloads.transport import _SafeRedirectHandler
from smart_launcher.infrastructure.stores import EventJournal, LogStore, ProgressStore


class _Response:
    def __init__(self, data: bytes, *, status: int = 200, headers=None) -> None:
        self._data = data
        self._position = 0
        self.status = status
        self.headers = headers or {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, amount: int) -> bytes:
        chunk = self._data[self._position : self._position + amount]
        self._position += len(chunk)
        return chunk


class _TimeoutResponse(_Response):
    def __init__(self, data: bytes, *, status: int = 200, headers=None) -> None:
        super().__init__(data, status=status, headers=headers)
        self._reads = 0

    def read(self, amount: int) -> bytes:
        self._reads += 1
        if self._reads > 1:
            raise TimeoutError("The read operation timed out")
        return super().read(amount)


class _QueueTransport:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.release_first = threading.Event()

    def download(self, request, *, on_progress, cancel_event):
        self.started.append(request.candidate.name)
        if len(self.started) == 1:
            while not self.release_first.wait(0.01):
                if cancel_event.is_set():
                    return DownloadResult(False, request, cancelled=True, detail="cancelled")
        on_progress(10, 10)
        return DownloadResult(
            True, request, request.destination / request.candidate.name, 10, "ok"
        )


class DownloadTransportTests(unittest.TestCase):
    def test_closed_controller_rejects_entry_without_phantom_event(self) -> None:
        journal = EventJournal()
        controller = DownloadController(
            ModelDownloadTransport(), LogStore(), journal, ProgressStore()
        )
        controller.close()

        request = DownloadRequest(
            DownloadCandidate(
                DownloadSource.DIRECT,
                "model.bin",
                "https://example.invalid/model.bin",
            ),
            Path("D:/Models"),
        )
        with self.assertRaises(RuntimeError):
            controller.enqueue(request)

        self.assertEqual(journal.snapshot(), ())

    def test_transport_finishes_atomically_and_never_overwrites_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "model.bin").write_bytes(b"old")
            request = DownloadRequest(
                DownloadCandidate(
                    DownloadSource.DIRECT, "model.bin", "https://example.invalid/model.bin"
                ),
                folder,
            )
            progress = []
            opener = unittest.mock.Mock()
            opener.open.return_value = _Response(b"new-content")
            result = ModelDownloadTransport(chunk_size=3, opener=opener).download(
                request, on_progress=lambda current, total: progress.append((current, total))
            )

            self.assertTrue(result.succeeded, result.detail)
            self.assertEqual((folder / "model.bin").read_bytes(), b"old")
            self.assertEqual((folder / "model (1).bin").read_bytes(), b"new-content")
            self.assertFalse(any(folder.glob("*.part-*")))
            self.assertEqual(progress[-1], (11, 11))

    def test_read_timeout_resumes_partial_file_with_range(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            request = DownloadRequest(
                DownloadCandidate(
                    DownloadSource.DIRECT,
                    "model.bin",
                    "https://files.example/model.bin",
                ),
                folder,
            )
            opener = unittest.mock.Mock()
            opener.open.side_effect = (
                _TimeoutResponse(b"abc", headers={"Content-Length": "6"}),
                _Response(
                    b"def",
                    status=206,
                    headers={
                        "Content-Length": "3",
                        "Content-Range": "bytes 3-5/6",
                    },
                ),
            )

            result = ModelDownloadTransport(
                chunk_size=3,
                retry_delay=0,
                opener=opener,
            ).download(request)

            self.assertTrue(result.succeeded, result.detail)
            self.assertEqual((folder / "model.bin").read_bytes(), b"abcdef")
            resumed_request = opener.open.call_args_list[1].args[0]
            self.assertEqual(resumed_request.get_header("Range"), "bytes=3-")

    def test_retry_budget_resets_after_each_successful_chunk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            request = DownloadRequest(
                DownloadCandidate(
                    DownloadSource.DIRECT,
                    "model.bin",
                    "https://files.example/model.bin",
                ),
                folder,
            )
            opener = unittest.mock.Mock()
            opener.open.side_effect = (
                _TimeoutResponse(b"abc", headers={"Content-Length": "9"}),
                _TimeoutResponse(
                    b"def",
                    status=206,
                    headers={
                        "Content-Length": "6",
                        "Content-Range": "bytes 3-8/9",
                    },
                ),
                _Response(
                    b"ghi",
                    status=206,
                    headers={
                        "Content-Length": "3",
                        "Content-Range": "bytes 6-8/9",
                    },
                ),
            )

            result = ModelDownloadTransport(
                chunk_size=3,
                max_retries=1,
                retry_delay=0,
                opener=opener,
            ).download(request)

            self.assertTrue(result.succeeded, result.detail)
            self.assertEqual((folder / "model.bin").read_bytes(), b"abcdefghi")
            ranges = [
                call.args[0].get_header("Range") for call in opener.open.call_args_list
            ]
            self.assertEqual(ranges, [None, "bytes=3-", "bytes=6-"])

    def test_cross_origin_redirect_drops_authorization_header(self) -> None:
        handler = _SafeRedirectHandler()
        request = urllib.request.Request(
            "https://civitai.com/api/download/models/1",
            headers={"Authorization": "Bearer secret", "User-Agent": "launcher"},
        )

        redirected = handler.redirect_request(
            request,
            None,
            307,
            "Temporary Redirect",
            {},
            "https://signed-storage.example/model.safetensors?signature=abc",
        )

        self.assertIsNotNone(redirected)
        self.assertIsNone(redirected.get_header("Authorization"))  # type: ignore[union-attr]
        self.assertEqual(redirected.get_header("User-agent"), "launcher")  # type: ignore[union-attr]

    def test_same_origin_redirect_keeps_authorization_header(self) -> None:
        handler = _SafeRedirectHandler()
        request = urllib.request.Request(
            "https://civitai.com/api/download/models/1",
            headers={"Authorization": "Bearer secret"},
        )

        redirected = handler.redirect_request(
            request,
            None,
            307,
            "Temporary Redirect",
            {},
            "https://civitai.com/api/download/models/2",
        )

        self.assertEqual(redirected.get_header("Authorization"), "Bearer secret")  # type: ignore[union-attr]

    def test_queue_automatically_starts_second_item_after_first(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = _QueueTransport()
            journal = EventJournal()
            progress = ProgressStore()
            controller = DownloadController(  # type: ignore[arg-type]
                transport, LogStore(), journal, progress
            )
            try:
                folder = Path(directory)
                for name in ("one.bin", "two.bin"):
                    controller.enqueue(
                        DownloadRequest(
                            DownloadCandidate(
                                DownloadSource.DIRECT,
                                name,
                                f"https://example.invalid/{name}",
                            ),
                            folder,
                        )
                    )
                self.assertTrue(self._wait(lambda: transport.started == ["one.bin"], 2.0))
                self.assertEqual(len(controller.snapshot()), 2)
                transport.release_first.set()
                self.assertTrue(
                    self._wait(lambda: transport.started == ["one.bin", "two.bin"], 2.0)
                )
                self.assertTrue(self._wait(lambda: not controller.snapshot(), 2.0))
                kinds = [entry.event.kind for entry in journal.snapshot()]
                self.assertEqual(kinds.count("DownloadCompleted"), 2)
                self.assertIsNone(progress.get(ProgressChannel.DOWNLOAD))
            finally:
                controller.close()

    def test_explicit_cancel_does_not_discard_queued_item(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = _QueueTransport()
            controller = DownloadController(  # type: ignore[arg-type]
                transport, LogStore(), EventJournal(), ProgressStore()
            )
            try:
                folder = Path(directory)
                for name in ("one.bin", "two.bin"):
                    controller.enqueue(
                        DownloadRequest(
                            DownloadCandidate(
                                DownloadSource.DIRECT,
                                name,
                                f"https://example.invalid/{name}",
                            ),
                            folder,
                        )
                    )
                self.assertTrue(self._wait(lambda: controller.running, 2.0))
                self.assertTrue(controller.cancel_active())
                self.assertTrue(
                    self._wait(lambda: transport.started == ["one.bin", "two.bin"], 2.0)
                )
                self.assertTrue(self._wait(lambda: not controller.snapshot(), 2.0))
            finally:
                transport.release_first.set()
                controller.close()

    @staticmethod
    def _wait(predicate, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return predicate()


if __name__ == "__main__":
    unittest.main()
