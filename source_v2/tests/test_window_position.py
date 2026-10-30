from __future__ import annotations

import unittest

from smart_launcher.ui.window_position import centered_position


class WindowPositionTests(unittest.TestCase):
    def test_centers_child_inside_parent_without_changing_dimensions(self) -> None:
        self.assertEqual(
            centered_position(200, 100, 960, 720, 720, 650),
            (320, 135),
        )

    def test_centers_main_window_on_screen(self) -> None:
        self.assertEqual(
            centered_position(0, 0, 1920, 1080, 960, 720),
            (480, 180),
        )


if __name__ == "__main__":
    unittest.main()
