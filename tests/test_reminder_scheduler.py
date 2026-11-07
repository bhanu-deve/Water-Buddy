import time
import unittest
from datetime import datetime, time as clock_time

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from water_assistant.reminder_schedule import ReminderSettings
from water_assistant.reminder_scheduler import ReminderScheduler

from .fakes import FakeClock

app = QApplication.instance() or QApplication([])


def at(hour, minute=0, second=0, day=7):
    return datetime(2026, 11, day, hour, minute, second)


class SchedulerTestCase(unittest.TestCase):
    """A ReminderScheduler on a clock that the test moves by hand; the timer's own wake-ups are triggered manually."""

    def make(self, now=None, settings=None, *, start=True):
        self.clock = FakeClock(now or at(10, 0))
        self.scheduler = ReminderScheduler(settings, clock=self.clock)
        self.addCleanup(self.scheduler.stop)
        self.reminders = 0
        self.changes = 0
        self.scheduler.reminderDue.connect(self._count_reminder)
        self.scheduler.stateChanged.connect(self._count_change)
        if start:
            self.scheduler.start()
        return self.scheduler

    def _count_reminder(self):
        self.reminders += 1

    def _count_change(self):
        self.changes += 1

    def wake_up(self):
        """What the timer does when it fires."""
        self.scheduler._on_timer()


class StartingTest(SchedulerTestCase):
    def test_nothing_is_planned_before_start(self):
        scheduler = self.make(start=False)
        self.assertFalse(scheduler.running)
        self.assertIsNone(scheduler.next_due)
        self.assertFalse(scheduler._timer.isActive())

    def test_start_plans_the_first_reminder_and_sets_the_timer(self):
        scheduler = self.make()
        self.assertTrue(scheduler.running)
        self.assertEqual(scheduler.next_due, at(10, 45))
        self.assertTrue(scheduler._timer.isActive())
        self.assertEqual(self.changes, 1)
        self.assertEqual(self.reminders, 0)

    def test_the_default_settings_are_the_ones_from_the_brief(self):
        scheduler = self.make()
        self.assertEqual(scheduler.settings, ReminderSettings())

    def test_the_timer_never_sleeps_longer_than_twenty_seconds(self):
        scheduler = self.make()  # the reminder is 45 minutes away
        self.assertLessEqual(scheduler._timer.interval(), 20_000 + 10)
        self.assertGreater(scheduler._timer.interval(), 19_000)

    def test_when_the_reminder_is_close_the_timer_waits_just_that_long(self):
        scheduler = self.make()
        self.clock.advance(minutes=44, seconds=55)  # five seconds to go
        scheduler._arm()
        self.assertTrue(4_900 <= scheduler._timer.interval() <= 5_100, scheduler._timer.interval())

    def test_now_is_the_clock_that_was_given(self):
        scheduler = self.make()
        self.assertEqual(scheduler.now(), at(10, 0))


class GivingRemindersTest(SchedulerTestCase):
    def test_a_reminder_is_given_when_it_is_due_and_the_next_one_is_planned(self):
        scheduler = self.make()
        self.clock.advance(minutes=45)
        self.wake_up()
        self.assertEqual(self.reminders, 1)
        self.assertEqual(scheduler.next_due, at(11, 30))
        self.assertTrue(scheduler._timer.isActive())

    def test_waking_up_before_the_time_gives_nothing_and_keeps_waiting(self):
        scheduler = self.make()
        self.clock.advance(minutes=30)
        self.wake_up()
        self.assertEqual(self.reminders, 0)
        self.assertEqual(scheduler.next_due, at(10, 45))
        self.assertTrue(scheduler._timer.isActive())

    def test_a_timer_that_fires_a_hair_early_does_not_remind_yet_and_does_not_spin(self):
        scheduler = self.make()
        self.clock.advance(minutes=44, seconds=59, milliseconds=990)  # 10 ms early
        self.wake_up()
        self.assertEqual(self.reminders, 0)
        self.assertGreaterEqual(scheduler._timer.interval(), 50)  # it waits a moment instead of spinning

    def test_sleeping_through_many_reminders_gives_just_one(self):
        self.make()
        self.clock.advance(hours=7)
        self.wake_up()
        self.assertEqual(self.reminders, 1)
        self.wake_up()
        self.assertEqual(self.reminders, 1)

    def test_nothing_is_given_outside_the_daily_window_until_it_opens(self):
        scheduler = self.make(at(21, 50))
        self.assertEqual(scheduler.next_due, at(8, 0, day=8))
        self.clock.now = at(23, 0)
        self.wake_up()
        self.assertEqual(self.reminders, 0)
        self.assertEqual(scheduler._timer.interval(), 20_005)  # overnight it only wakes up every 20 seconds
        self.clock.now = at(7, 59, 59, day=8)
        self.wake_up()
        self.assertEqual(self.reminders, 0)
        self.assertTrue(1_000 <= scheduler._timer.interval() <= 1_010, scheduler._timer.interval())  # one second to go
        self.clock.now = at(8, 0, day=8)
        self.wake_up()
        self.assertEqual(self.reminders, 1)

    def test_a_late_timer_event_after_stop_does_nothing(self):
        scheduler = self.make()
        scheduler.stop()
        self.clock.advance(hours=2)
        self.wake_up()
        self.assertEqual(self.reminders, 0)
        self.assertFalse(scheduler._timer.isActive())


class PauseSnoozeResetTest(SchedulerTestCase):
    def test_pausing_stops_the_timer_and_the_reminders(self):
        scheduler = self.make()
        before = self.changes
        scheduler.pause()
        self.assertTrue(scheduler.paused)
        self.assertIsNone(scheduler.next_due)
        self.assertFalse(scheduler._timer.isActive())  # no wake-ups at all while paused
        self.assertEqual(self.changes, before + 1)
        self.clock.advance(hours=2)
        self.wake_up()
        self.assertEqual(self.reminders, 0)

    def test_resuming_starts_a_fresh_interval(self):
        scheduler = self.make()
        scheduler.pause()
        self.clock.advance(minutes=20)
        scheduler.resume()
        self.assertFalse(scheduler.paused)
        self.assertEqual(scheduler.next_due, at(11, 5))
        self.assertTrue(scheduler._timer.isActive())

    def test_snooze_postpones_the_reminder(self):
        scheduler = self.make()
        self.clock.advance(minutes=40)  # 10:40: the planned 10:45 reminder is 5 minutes away
        self.assertEqual(scheduler.snooze(20), at(11, 0))
        self.assertEqual(scheduler.next_due, at(11, 0))
        self.clock.now = at(10, 50)
        self.wake_up()
        self.assertEqual(self.reminders, 0)
        self.clock.now = at(11, 0)
        self.wake_up()
        self.assertEqual(self.reminders, 1)

    def test_snoozing_while_paused_changes_nothing(self):
        scheduler = self.make()
        scheduler.pause()
        self.assertIsNone(scheduler.snooze(10))
        self.assertTrue(scheduler.paused)

    def test_reset_starts_the_interval_again_from_now(self):
        scheduler = self.make()
        self.clock.advance(minutes=30)
        scheduler.reset()
        self.assertEqual(scheduler.next_due, at(11, 15))

    def test_new_settings_apply_from_now(self):
        scheduler = self.make()
        self.clock.advance(minutes=10)
        scheduler.set_settings(ReminderSettings(interval_minutes=15))
        self.assertEqual(scheduler.settings.interval_minutes, 15)
        self.assertEqual(scheduler.next_due, at(10, 25))

    def test_a_handler_that_snoozes_during_the_reminder_is_respected(self):
        scheduler = self.make()
        scheduler.reminderDue.connect(lambda: scheduler.snooze(10))  # for example "dismiss: ask me again in 10 minutes"
        self.clock.advance(minutes=45)
        self.wake_up()
        self.assertEqual(scheduler.next_due, at(10, 55))

    def test_a_handler_that_pauses_during_the_reminder_is_respected(self):
        scheduler = self.make()
        scheduler.reminderDue.connect(scheduler.pause)
        self.clock.advance(minutes=45)
        self.wake_up()
        self.assertTrue(scheduler.paused)
        self.assertFalse(scheduler._timer.isActive())


class RemindNowTest(SchedulerTestCase):
    def test_remind_now_gives_a_reminder_at_once_and_restarts_the_interval(self):
        scheduler = self.make()
        self.clock.advance(minutes=10)
        scheduler.remind_now()
        self.assertEqual(self.reminders, 1)
        self.assertEqual(scheduler.next_due, at(10, 55))

    def test_remind_now_works_while_paused_and_leaves_it_paused(self):
        scheduler = self.make()
        scheduler.pause()
        scheduler.remind_now()
        self.assertEqual(self.reminders, 1)
        self.assertTrue(scheduler.paused)
        self.assertIsNone(scheduler.next_due)

    def test_remind_now_works_before_start_without_starting_anything(self):
        scheduler = self.make(start=False)
        scheduler.remind_now()
        self.assertEqual(self.reminders, 1)
        self.assertIsNone(scheduler.next_due)
        self.assertFalse(scheduler._timer.isActive())


class RealTimeTest(unittest.TestCase):
    """With the real clock and the real Qt timer, at a very short interval (0.025 minutes = 1.5 seconds).

    The window is "all day" (start == stop): with the normal 08:00-22:00 window these tests would only pass during
    the day, because at night the next reminder really is "tomorrow at 08:00"."""

    INTERVAL = ReminderSettings(interval_minutes=0.025, start=clock_time(0, 0), stop=clock_time(0, 0))

    def setUp(self):
        self.scheduler = ReminderScheduler(self.INTERVAL)
        self.addCleanup(self.scheduler.stop)
        self.arrivals = []
        self.scheduler.reminderDue.connect(lambda: self.arrivals.append(time.monotonic()))

    def wait_for_arrivals(self, count, timeout=6.0):
        deadline = time.monotonic() + timeout
        while len(self.arrivals) < count and time.monotonic() < deadline:
            QTest.qWait(10)
        return len(self.arrivals) >= count

    def test_reminders_arrive_on_time_and_keep_coming(self):
        started = time.monotonic()
        self.scheduler.start()
        self.assertTrue(self.wait_for_arrivals(2))
        self.assertTrue(1.3 <= self.arrivals[0] - started <= 2.2, self.arrivals[0] - started)
        self.assertTrue(1.3 <= self.arrivals[1] - self.arrivals[0] <= 2.2, self.arrivals[1] - self.arrivals[0])

    def test_pause_really_stops_them_and_resume_brings_them_back(self):
        self.scheduler.start()
        self.assertTrue(self.wait_for_arrivals(1))
        self.scheduler.pause()
        QTest.qWait(2300)  # more than one interval
        self.assertEqual(len(self.arrivals), 1)
        resumed = time.monotonic()
        self.scheduler.resume()
        self.assertTrue(self.wait_for_arrivals(2))
        self.assertTrue(1.3 <= self.arrivals[1] - resumed <= 2.2, self.arrivals[1] - resumed)

    def test_snooze_postpones_a_real_reminder(self):
        started = time.monotonic()
        self.scheduler.start()
        self.scheduler.snooze(0.05)  # 3 seconds instead of the planned 1.5
        self.assertTrue(self.wait_for_arrivals(1))
        self.assertTrue(2.7 <= self.arrivals[0] - started <= 3.8, self.arrivals[0] - started)


if __name__ == "__main__":
    unittest.main()
