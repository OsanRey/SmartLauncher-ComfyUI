from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import tempfile
import unittest

from smart_launcher.domain.events import (
    DomainEvent,
    EventSeverity,
    JournalEntry,
    LogRecord,
)
from smart_launcher.infrastructure.diagnostic_log import DiagnosticLog


class DiagnosticLogTests(unittest.TestCase):
    def test_redacts_credentials_and_skips_transient_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = DiagnosticLog(
                Path(directory),
                secrets=("known-secret",),
            )
            now = datetime.now(UTC)
            log.append_log(
                LogRecord(
                    1,
                    now,
                    "download",
                    "stderr",
                    "Authorization: Bearer abc123 "
                    "https://example.test/file?token=url-secret "
                    "api_key=field-secret known-secret",
                )
            )
            log.append_log(
                LogRecord(
                    2,
                    now,
                    "comfyui",
                    "stdout",
                    "73% generation progress",
                )
            )
            log.append_log(
                LogRecord(
                    3,
                    now,
                    "comfyui",
                    "stdout",
                    "40%|████ | 12/30\r[INFO] Prompt executed\n",
                )
            )
            log.append_log(
                LogRecord(4, now, "comfyui", "stdout", "temporary", transient=True)
            )
            log.close()

            content = (Path(directory) / "launcher.log").read_text(encoding="utf-8")

        self.assertNotIn("abc123", content)
        self.assertNotIn("url-secret", content)
        self.assertNotIn("field-secret", content)
        self.assertNotIn("known-secret", content)
        self.assertNotIn("73% generation progress", content)
        self.assertNotIn("40%", content)
        self.assertNotIn("temporary", content)
        self.assertIn("Prompt executed", content)
        self.assertGreaterEqual(content.count("<redacted>"), 4)

    def test_records_structured_events_and_rotates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = DiagnosticLog(Path(directory), max_bytes=180, backup_count=2)
            for sequence in range(8):
                log.append_event(
                    JournalEntry(
                        sequence,
                        DomainEvent(
                            "Failure",
                            "Операция завершилась с ошибкой",
                            EventSeverity.ERROR,
                            "x" * 80,
                        ),
                    )
                )
            log.close()
            files = tuple(Path(directory).glob("launcher.log*"))
            combined = "".join(path.read_text(encoding="utf-8") for path in files)

        self.assertLessEqual(len(files), 3)
        self.assertIn("event/error", combined)

    def test_close_is_idempotent_and_late_records_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = DiagnosticLog(Path(directory))
            log.close()
            log.close()
            log.append_log(
                LogRecord(
                    1,
                    datetime.now(UTC),
                    "late-worker",
                    "stderr",
                    "must not be written",
                )
            )
            content = (Path(directory) / "launcher.log").read_text(encoding="utf-8")

        self.assertEqual(content.count("session ended"), 1)
        self.assertNotIn("must not be written", content)


if __name__ == "__main__":
    unittest.main()
