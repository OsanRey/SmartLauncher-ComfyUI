from __future__ import annotations

import unittest

from smart_launcher.domain.events import (
    DomainEvent,
    ProgressChannel,
    ProgressMode,
    ProgressState,
)
from smart_launcher.infrastructure.event_bus import EventBus
from smart_launcher.infrastructure.stores import (
    EventJournal,
    LogStore,
    ProgressStore,
    should_follow_autoscroll,
)
from smart_launcher.ui.projection import UiProjection
from smart_launcher.ui.main_window import _event_count_text


class StoreTests(unittest.TestCase):
    def test_event_count_uses_russian_plural_forms(self) -> None:
        self.assertEqual(_event_count_text(1), "1 событие")
        self.assertEqual(_event_count_text(2), "2 события")
        self.assertEqual(_event_count_text(5), "5 событий")
        self.assertEqual(_event_count_text(11), "11 событий")
        self.assertEqual(_event_count_text(22), "22 события")

    def test_log_store_is_bounded_and_keeps_monotonic_sequence(self) -> None:
        store = LogStore(capacity=2)
        store.append(source="a", stream="stdout", text="one")
        second = store.append(source="a", stream="stdout", text="two")
        third = store.append(source="a", stream="stderr", text="three")

        self.assertEqual(store.snapshot(), (second, third))
        self.assertEqual([record.sequence for record in store.snapshot()], [2, 3])

    def test_log_store_removes_ansi_terminal_sequences(self) -> None:
        store = LogStore()

        record = store.append(
            source="comfyui",
            stream="stdout",
            text="\x1b[32m[INFO]\x1b[0m Starting server\n",
        )

        self.assertEqual(record.text, "[INFO] Starting server\n")

    def test_view_snapshots_do_not_consume_console_or_events(self) -> None:
        logs = LogStore()
        events = EventJournal()
        logs.append(source="comfyui", stream="stdout", text="raw")
        events.append(DomainEvent("ServerReady", "ComfyUI ready"))

        first_logs = logs.snapshot()
        first_events = events.snapshot()

        self.assertEqual(logs.snapshot(), first_logs)
        self.assertEqual(events.snapshot(), first_events)

    def test_stale_clear_cannot_hide_new_progress_in_same_channel(self) -> None:
        store = ProgressStore()
        old = ProgressState(
            ProgressChannel.MAINTENANCE,
            "old",
            "Updating",
            ProgressMode.INDETERMINATE,
        )
        new = ProgressState(
            ProgressChannel.MAINTENANCE,
            "new",
            "Checking packages",
            ProgressMode.DETERMINATE,
            2,
            5,
        )
        store.set(old)
        store.set(new)

        self.assertFalse(store.clear(ProgressChannel.MAINTENANCE, "old"))
        self.assertIs(store.get(ProgressChannel.MAINTENANCE), new)

    def test_progress_channels_are_independent(self) -> None:
        store = ProgressStore()
        generation = ProgressState(
            ProgressChannel.GENERATION,
            "prompt-a",
            "Generating",
            ProgressMode.DETERMINATE,
            4,
            10,
        )
        download = ProgressState(
            ProgressChannel.DOWNLOAD,
            "file-b",
            "Downloading",
            ProgressMode.DETERMINATE,
            20,
            100,
        )
        store.set(generation)
        store.set(download)
        store.clear(ProgressChannel.GENERATION, "prompt-a")

        self.assertIsNone(store.get(ProgressChannel.GENERATION))
        self.assertIs(store.get(ProgressChannel.DOWNLOAD), download)

    def test_event_bus_unsubscribe_is_idempotent(self) -> None:
        bus = EventBus()
        received = []
        unsubscribe = bus.subscribe(received.append)
        bus.publish(DomainEvent("one", "One"))
        unsubscribe()
        unsubscribe()
        bus.publish(DomainEvent("two", "Two"))

        self.assertEqual([event.kind for event in received], ["one"])

    def test_autoscroll_requires_setting_and_near_bottom_position(self) -> None:
        self.assertTrue(should_follow_autoscroll(enabled=True, distance_from_bottom=5))
        self.assertFalse(should_follow_autoscroll(enabled=False, distance_from_bottom=0))
        self.assertFalse(should_follow_autoscroll(enabled=True, distance_from_bottom=100))

    def test_projection_drains_in_batches_without_deleting_history(self) -> None:
        logs = LogStore()
        events = EventJournal()
        progress = ProgressStore()
        for number in range(5):
            logs.append(source="test", stream="stdout", text=str(number))
            events.append(DomainEvent(f"event-{number}", str(number)))
        projection = UiProjection(logs, events, progress, batch_limit=2)

        first = projection.drain()
        second = projection.drain()

        self.assertEqual([record.text for record in first.logs], ["0", "1"])
        self.assertEqual([record.text for record in second.logs], ["2", "3"])
        self.assertEqual(len(logs.snapshot()), 5)
        self.assertEqual(len(events.snapshot()), 5)

    def test_new_projection_can_rebuild_tabs_from_persistent_history(self) -> None:
        logs = LogStore()
        events = EventJournal()
        progress = ProgressStore()
        logs.append(source="test", stream="stdout", text="kept")
        events.append(DomainEvent("kept", "Kept"))

        first_view = UiProjection(logs, events, progress).drain()
        second_view = UiProjection(logs, events, progress).drain()

        self.assertEqual(first_view.logs, second_view.logs)
        self.assertEqual(first_view.events, second_view.events)


if __name__ == "__main__":
    unittest.main()
