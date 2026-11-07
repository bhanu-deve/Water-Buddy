import unittest
from datetime import datetime

from PySide6.QtWidgets import QApplication, QMenu

from water_assistant.reminder_menu import (
    describe_next_reminder,
    format_minutes,
    populate_reminder_menu,
    populate_reminders_submenu,
)
from water_assistant.reminder_schedule import ReminderSettings
from water_assistant.reminder_scheduler import ReminderScheduler

from .fakes import FakeClock

app = QApplication.instance() or QApplication([])


def at(hour, minute=0, second=0, day=7):
    return datetime(2026, 11, day, hour, minute, second)


class FormatMinutesTest(unittest.TestCase):
    def test_minutes_and_hours(self):
        self.assertEqual(format_minutes(1), "1 min")
        self.assertEqual(format_minutes(23), "23 min")
        self.assertEqual(format_minutes(59), "59 min")
        self.assertEqual(format_minutes(60), "1 h")
        self.assertEqual(format_minutes(65), "1 h 05 min")
        self.assertEqual(format_minutes(125), "2 h 05 min")
        self.assertEqual(format_minutes(120), "2 h")


class MenuTestCase(unittest.TestCase):
    def make(self, now=None, settings=None):
        self.clock = FakeClock(now or at(10, 0))
        self.scheduler = ReminderScheduler(settings, clock=self.clock)
        self.addCleanup(self.scheduler.stop)
        self.scheduler.start()
        self.reminders = []
        self.scheduler.reminderDue.connect(lambda: self.reminders.append(self.clock()))
        return self.scheduler

    def menu(self, **options) -> QMenu:
        menu = QMenu()
        self.addCleanup(menu.deleteLater)
        populate_reminder_menu(menu, self.scheduler, **options)
        return menu

    @staticmethod
    def entry(menu, text):
        return next(action for action in menu.actions() if action.text() == text)

    @staticmethod
    def titles(menu):
        return ["-" if action.isSeparator() else action.text() for action in menu.actions()]


class DescribeTest(MenuTestCase):
    def test_the_next_reminder_in_minutes(self):
        self.make()
        self.assertEqual(describe_next_reminder(self.scheduler), "Next reminder 10:45 (in 45 min)")
        self.clock.advance(minutes=5)
        self.assertEqual(describe_next_reminder(self.scheduler), "Next reminder 10:45 (in 40 min)")

    def test_a_partial_minute_counts_as_a_whole_one(self):
        self.make()
        self.clock.advance(minutes=44, seconds=30)
        self.assertEqual(describe_next_reminder(self.scheduler), "Next reminder 10:45 (in 1 min)")

    def test_an_overdue_reminder_still_reads_sensibly(self):
        self.make()
        self.clock.advance(minutes=50)
        self.assertEqual(describe_next_reminder(self.scheduler), "Next reminder 10:45 (in 1 min)")

    def test_more_than_an_hour_away_is_shown_in_hours(self):
        self.make(at(8, 0), ReminderSettings(interval_minutes=90))
        self.assertEqual(describe_next_reminder(self.scheduler), "Next reminder 09:30 (in 1 h 30 min)")

    def test_tomorrow_morning_in_the_evening(self):
        self.make(at(21, 50))
        self.assertEqual(describe_next_reminder(self.scheduler), "Next reminder tomorrow 08:00")

    def test_a_date_when_it_is_more_than_a_day_away(self):
        self.make(at(23, 0), ReminderSettings(interval_minutes=24 * 60))  # tomorrow 23:00 is outside the window
        self.assertEqual(describe_next_reminder(self.scheduler), "Next reminder 09 Nov 08:00")

    def test_paused_and_not_started(self):
        self.make()
        self.scheduler.pause()
        self.assertEqual(describe_next_reminder(self.scheduler), "Reminders are paused")
        fresh = ReminderScheduler(clock=self.clock)
        self.addCleanup(fresh.stop)
        self.assertEqual(describe_next_reminder(fresh), "Reminders are off")


class ReminderEntriesTest(MenuTestCase):
    def test_the_small_menu_entries_follow_the_brief(self):
        self.make()
        self.assertEqual(
            self.titles(self.menu()),
            ["Next reminder 10:45 (in 45 min)", "Snooze for 10 minutes", "Snooze for 20 minutes", "Pause reminders", "Resume reminders"],
        )

    def test_the_first_line_is_information_and_cannot_be_clicked(self):
        self.make()
        self.assertFalse(self.menu().actions()[0].isEnabled())

    def test_snooze_for_10_and_20_minutes_postpones_the_reminder(self):
        self.make()
        self.clock.advance(minutes=3)
        self.entry(self.menu(), "Snooze for 10 minutes").trigger()
        self.assertEqual(self.scheduler.next_due, at(10, 13))
        self.entry(self.menu(), "Snooze for 20 minutes").trigger()
        self.assertEqual(self.scheduler.next_due, at(10, 23))

    def test_pause_and_resume(self):
        self.make()
        menu = self.menu()
        self.assertTrue(self.entry(menu, "Pause reminders").isEnabled())
        self.assertFalse(self.entry(menu, "Resume reminders").isEnabled())  # nothing to resume yet
        self.entry(menu, "Pause reminders").trigger()
        self.assertTrue(self.scheduler.paused)

        menu = self.menu()  # opening the menu again shows the new state
        self.assertEqual(menu.actions()[0].text(), "Reminders are paused")
        self.assertFalse(self.entry(menu, "Pause reminders").isEnabled())
        self.assertTrue(self.entry(menu, "Resume reminders").isEnabled())
        self.assertFalse(self.entry(menu, "Snooze for 10 minutes").isEnabled())  # nothing to postpone while paused
        self.assertFalse(self.entry(menu, "Snooze for 20 minutes").isEnabled())
        self.clock.advance(minutes=5)
        self.entry(menu, "Resume reminders").trigger()
        self.assertFalse(self.scheduler.paused)
        self.assertEqual(self.scheduler.next_due, at(10, 50))

    def test_the_small_menu_has_no_remind_me_now(self):
        self.make()
        self.assertNotIn("Remind me now", self.titles(self.menu()))

    def test_remind_me_now_gives_a_reminder_at_once(self):
        self.make()
        menu = self.menu(remind_now=True)
        self.assertEqual(self.titles(menu)[-2:], ["-", "Remind me now"])
        self.entry(menu, "Remind me now").trigger()
        self.assertEqual(self.reminders, [at(10, 0)])
        self.assertEqual(self.scheduler.next_due, at(10, 45))  # and the normal rhythm starts again from now

    def test_the_right_click_submenu_has_everything(self):
        self.make()
        menu = QMenu()
        self.addCleanup(menu.deleteLater)
        populate_reminders_submenu(menu, self.scheduler)
        self.assertEqual(self.titles(menu), ["Reminders"])
        submenu = menu.actions()[0].menu()
        self.assertEqual(
            self.titles(submenu),
            [
                "Next reminder 10:45 (in 45 min)",
                "Snooze for 10 minutes",
                "Snooze for 20 minutes",
                "Pause reminders",
                "Resume reminders",
                "-",
                "Remind me now",
            ],
        )


if __name__ == "__main__":
    unittest.main()
