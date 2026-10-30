from __future__ import annotations

import unittest

from smart_launcher.ui.progress_view import _activity_segment_bounds, _progress_layout


class ProgressViewTests(unittest.TestCase):
    def test_activity_segment_enters_and_leaves_view_in_one_direction(self) -> None:
        start = _activity_segment_bounds(100.0, 0.0)
        middle = _activity_segment_bounds(100.0, 0.5)
        end = _activity_segment_bounds(100.0, 1.0)

        self.assertLessEqual(start[1], 0.0)
        self.assertGreater(middle[0], start[0])
        self.assertGreater(middle[1], start[1])
        self.assertGreaterEqual(end[0], 100.0)

    def test_single_progress_uses_full_width_and_multiple_items_use_stable_grid(self) -> None:
        self.assertEqual(_progress_layout(1), ((0, 0, 2),))
        self.assertEqual(_progress_layout(2), ((0, 0, 1), (0, 1, 1)))
        self.assertEqual(
            _progress_layout(3),
            ((0, 0, 1), (0, 1, 1), (1, 0, 2)),
        )
        self.assertEqual(
            _progress_layout(4),
            ((0, 0, 1), (0, 1, 1), (1, 0, 1), (1, 1, 1)),
        )


if __name__ == "__main__":
    unittest.main()
