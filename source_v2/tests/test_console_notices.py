import unittest

from smart_launcher.application.console_notices import ConsoleNotices


class ConsoleNoticeTests(unittest.TestCase):
    def test_dependency_command_keeps_reason_and_nearby_context_across_chunks(self):
        events = []
        notices = ConsoleNotices(events.append, "session")
        message = (
            "[INFO] [Anima 3-8B] standalone Semantic Connector v2 support installed (run window: 64)\n"
            "Module 'diffusers' load failed. If you don't have it installed, do it:\n"
            "pip install diffusers\n"
        )
        for char in message:
            notices.feed("stdout", char)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].title, "Не удалось загрузить модуль diffusers")
        self.assertEqual(events[0].detail, message.strip())
        notices.feed("stdout", message)
        self.assertEqual(len(events), 1)

    def test_generic_command_context_does_not_cross_blank_lines_or_streams(self):
        events = []
        notices = ConsoleNotices(events.append, "session")
        notices.feed("stdout", "unrelated old line\n\nInstall the optional feature:\n")
        notices.feed("stderr", "other stream\n")
        notices.feed("stdout", "pip install example\n")
        self.assertEqual(events[0].detail, "Install the optional feature:\npip install example")
        notices.feed("stdout", "pip install second\n")
        self.assertEqual(events[1].detail, "pip install second")

    def test_fragmented_matrix_warning_includes_command_and_deduplicates(self):
        events = []
        notices = ConsoleNotices(events.append, "session")
        message = (
            "[WARNING] [ComfyUI-Manager] The matrix sharing feature has been disabled "
            "because the `matrix-nio` dependency is not installed.\n"
            "\tTo use this feature, please run the following command:\n"
            "\tD:\\ComfyUI\\venv\\Scripts\\python.exe -m pip install matrix-nio\n"
        )
        for char in message:
            notices.feed("stderr", char)
        self.assertEqual(len(events), 1)
        self.assertIn("необязательно", events[0].title)
        self.assertIn("python.exe -m pip install matrix-nio", events[0].detail)
        self.assertEqual(events[0].operation_id, "session")
        notices.feed("stderr", message)
        notices.flush()
        self.assertEqual(len(events), 1)

    def test_streams_are_separate_and_normal_output_is_not_promoted(self):
        events = []
        notices = ConsoleNotices(events.append, "session")
        notices.feed("stderr", "[WARNING] Missing dependency\n")
        notices.feed("stdout", "[INFO] Normal output\nprogress 100%\n")
        notices.feed("stderr", "Please install it manually\n\n")
        self.assertEqual(len(events), 1)
        self.assertIn("manually", events[0].detail)
        self.assertNotIn("Normal output", events[0].detail)

    def test_exit_flushes_tail_and_ansi_is_removed(self):
        events = []
        notices = ConsoleNotices(events.append, "session")
        notices.feed("stderr", "\x1b[33m[WARNING] Something missing\x1b[0m")
        notices.flush()
        self.assertEqual(len(events), 1)
        self.assertNotIn("\x1b", events[0].detail)

    def test_bare_pip_command_is_visible_and_new_session_resets_duplicates(self):
        events = []
        for session in ("one", "two"):
            notices = ConsoleNotices(events.append, session)
            notices.feed("stdout", "pip install diffusers\n")
        self.assertEqual(len(events), 2)
        self.assertIn("Зависимости", events[0].title)
