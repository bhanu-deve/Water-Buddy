import contextlib
import io
import unittest
from datetime import time

from water_assistant.app import parse_arguments, reminder_settings
from water_assistant.reminder_schedule import ReminderSettings


class ParseArgumentsTest(unittest.TestCase):
    def test_defaults(self):
        options, rest = parse_arguments([])
        self.assertFalse(options.no_voice)
        self.assertIsNone(options.volume)
        self.assertEqual(rest, [])

    def test_no_voice(self):
        options, _ = parse_arguments(["--no-voice"])
        self.assertTrue(options.no_voice)

    def test_volume_in_percent(self):
        for text, expected in (("0", 0), ("35", 35), ("100", 100)):
            options, _ = parse_arguments(["--volume", text])
            self.assertEqual(options.volume, expected)

    def test_volume_outside_0_to_100_is_refused(self):
        for text in ("-1", "101", "loud"):
            with contextlib.redirect_stderr(io.StringIO()) as errors, self.assertRaises(SystemExit):
                parse_arguments(["--volume", text])
            self.assertIn("--volume", errors.getvalue())

    def test_reminder_interval_in_minutes(self):
        options, _ = parse_arguments([])
        self.assertIsNone(options.reminder_interval)
        for text, expected in (("45", 45.0), ("0.5", 0.5), ("0.02", 0.02), ("1440", 1440.0)):
            options, _ = parse_arguments(["--reminder-interval", text])
            self.assertEqual(options.reminder_interval, expected)

    def test_nonsense_reminder_intervals_are_refused(self):
        for text in ("0", "-1", "0.001", "1441", "abc", "nan", "inf", ""):
            with contextlib.redirect_stderr(io.StringIO()) as errors, self.assertRaises(SystemExit):
                parse_arguments(["--reminder-interval", text])
            self.assertIn("--reminder-interval", errors.getvalue(), text)

    def test_reminder_hours(self):
        options, _ = parse_arguments([])
        self.assertIsNone(options.reminder_hours)
        for text, expected in (
            ("08:00-22:00", (time(8, 0), time(22, 0))),
            ("7:30-23:15", (time(7, 30), time(23, 15))),
            ("00:00-00:00", (time(0, 0), time(0, 0))),  # all day
            ("22:00-06:00", (time(22, 0), time(6, 0))),  # overnight
        ):
            options, _ = parse_arguments(["--reminder-hours", text])
            self.assertEqual(options.reminder_hours, expected, text)

    def test_nonsense_reminder_hours_are_refused(self):
        for text in ("8-22", "25:00-22:00", "08:60-22:00", "08:00", "08:00-", "abc", "", "08:00 - 22:00x"):
            with contextlib.redirect_stderr(io.StringIO()) as errors, self.assertRaises(SystemExit):
                parse_arguments(["--reminder-hours", text])
            self.assertIn("--reminder-hours", errors.getvalue(), text)

    def test_the_reminder_settings_follow_the_brief_unless_options_change_them(self):
        options, _ = parse_arguments([])
        self.assertEqual(reminder_settings(options), ReminderSettings())  # 45 minutes, 08:00-22:00
        options, _ = parse_arguments(["--reminder-interval", "30"])
        self.assertEqual(reminder_settings(options), ReminderSettings(interval_minutes=30))
        options, _ = parse_arguments(["--reminder-hours", "07:00-23:00"])
        self.assertEqual(reminder_settings(options), ReminderSettings(start=time(7, 0), stop=time(23, 0)))
        options, _ = parse_arguments(["--reminder-interval", "0.5", "--reminder-hours", "00:00-00:00"])
        self.assertEqual(reminder_settings(options), ReminderSettings(interval_minutes=0.5, start=time(0, 0), stop=time(0, 0)))

    def test_options_meant_for_qt_are_passed_on(self):
        options, rest = parse_arguments(["--no-voice", "-style", "fusion"])
        self.assertTrue(options.no_voice)
        self.assertEqual(rest, ["-style", "fusion"])


if __name__ == "__main__":
    unittest.main()
