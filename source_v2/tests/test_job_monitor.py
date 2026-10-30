from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from smart_launcher.comfy.job_monitor import ComfyJobMonitor


class _SequenceClient:
    def __init__(self, responses):
        self._responses = iter(responses)
        self._last = {"jobs": []}

    def jobs(self, *, limit=200):
        try:
            self._last = next(self._responses)
        except StopIteration:
            pass
        return self._last


class JobMonitorTests(unittest.TestCase):
    def test_tracks_browser_job_without_replaying_old_history(self) -> None:
        old = {"id": "old", "status": "completed"}
        running = {"id": "new", "status": "in_progress"}
        completed = {"id": "new", "status": "completed"}
        client = _SequenceClient(
            [
                {"jobs": [old]},
                {"jobs": [running, old]},
                {"jobs": [completed, old]},
            ]
        )
        updates = []
        finished = threading.Event()
        monitor = ComfyJobMonitor(client, interval=0.01, retry_interval=0.01)  # type: ignore[arg-type]

        def receive(update):
            updates.append(update)
            if update.newly_terminal:
                finished.set()

        monitor.start(receive)
        try:
            self.assertTrue(finished.wait(2.0))
        finally:
            monitor.stop()

        self.assertTrue(any(update.active for update in updates))
        terminal_ids = [
            job.job_id for update in updates for job in update.newly_terminal
        ]
        self.assertEqual(terminal_ids, ["new"])

    def test_rejects_malformed_jobs_payload(self) -> None:
        with self.assertRaisesRegex(ValueError, "jobs list"):
            ComfyJobMonitor._parse_jobs({"jobs": {}})

    def test_stop_waits_until_concurrent_start_has_started_thread(self) -> None:
        real_thread = threading.Thread
        entered_start = threading.Event()
        release_start = threading.Event()
        failures: list[BaseException] = []

        class _DelayedStartThread(real_thread):
            def start(self) -> None:
                entered_start.set()
                release_start.wait(2.0)
                super().start()

        monitor = ComfyJobMonitor(
            _SequenceClient([{"jobs": []}]), interval=0.01, retry_interval=0.01
        )  # type: ignore[arg-type]

        def start_monitor() -> None:
            try:
                monitor.start(lambda _update: None)
            except BaseException as exc:
                failures.append(exc)

        with patch("smart_launcher.comfy.job_monitor.threading.Thread", _DelayedStartThread):
            starter = real_thread(target=start_monitor)
            starter.start()
            self.assertTrue(entered_start.wait(1.0))
            stopper = real_thread(target=monitor.stop)
            stopper.start()
            self.assertTrue(stopper.is_alive())
            release_start.set()
            starter.join(2.0)
            stopper.join(2.0)

        self.assertFalse(starter.is_alive())
        self.assertFalse(stopper.is_alive())
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
