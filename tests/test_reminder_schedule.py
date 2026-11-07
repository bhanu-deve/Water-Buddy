import dataclasses
import unittest
from datetime import datetime, time, timedelta

from water_assistant.reminder_schedule import (
    DEFAULT_INTERVAL_MINUTES,
    MAX_INTERVAL_MINUTES,
    MIN_INTERVAL_MINUTES,
    ReminderSchedule,
    ReminderSettings,
)


def at(hour, minute=0, second=0, day=7, month=11, year=2026):
    return datetime(year, month, day, hour, minute, second)


OVERNIGHT = ReminderSettings(start=time(22, 0), stop=time(6, 0))


class SettingsTest(unittest.TestCase):
    def test_the_defaults_are_the_ones_from_the_brief(self):
        settings = ReminderSettings()
        self.assertEqual(settings.interval_minutes, 45)
        self.assertEqual(DEFAULT_INTERVAL_MINUTES, 45)
        self.assertEqual((settings.start, settings.stop), (time(8, 0), time(22, 0)))
        self.assertEqual(settings.interval, timedelta(minutes=45))

    def test_whole_numbers_are_stored_as_floats(self):
        settings = ReminderSettings(interval_minutes=30)
        self.assertIsInstance(settings.interval_minutes, float)
        self.assertEqual(settings.interval, timedelta(minutes=30))

    def test_the_limits_themselves_are_allowed(self):
        ReminderSettings(interval_minutes=MIN_INTERVAL_MINUTES)
        ReminderSettings(interval_minutes=MAX_INTERVAL_MINUTES)

    def test_nonsense_intervals_are_refused(self):
        for bad in (0, -5, MIN_INTERVAL_MINUTES / 2, MAX_INTERVAL_MINUTES + 1, float("nan"), float("inf"), True, "45", None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                ReminderSettings(interval_minutes=bad)

    def test_nonsense_times_are_refused(self):
        for bad in ("8:00", 8, None, datetime(2026, 11, 7, 8, 0)):
            with self.assertRaises(ValueError, msg=repr(bad)):
                ReminderSettings(start=bad)
            with self.assertRaises(ValueError, msg=repr(bad)):
                ReminderSettings(stop=bad)

    def test_settings_cannot_be_changed_afterwards(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            ReminderSettings().interval_minutes = 10


class WindowTest(unittest.TestCase):
    def test_the_daily_window_includes_the_start_and_excludes_the_stop(self):
        settings = ReminderSettings()
        self.assertFalse(settings.is_active(at(7, 59, 59)))
        self.assertTrue(settings.is_active(at(8, 0)))
        self.assertTrue(settings.is_active(at(12, 0)))
        self.assertTrue(settings.is_active(at(21, 59, 59)))
        self.assertFalse(settings.is_active(at(22, 0)))
        self.assertFalse(settings.is_active(at(0, 0)))

    def test_fractions_of_a_second_count(self):
        settings = ReminderSettings()
        self.assertTrue(settings.is_active(datetime(2026, 11, 7, 21, 59, 59, 999999)))
        self.assertFalse(settings.is_active(datetime(2026, 11, 7, 22, 0, 0, 1)))

    def test_an_overnight_window_runs_through_midnight(self):
        self.assertTrue(OVERNIGHT.is_active(at(22, 0)))
        self.assertTrue(OVERNIGHT.is_active(at(23, 30)))
        self.assertTrue(OVERNIGHT.is_active(at(0, 0)))
        self.assertTrue(OVERNIGHT.is_active(at(5, 59, 59)))
        self.assertFalse(OVERNIGHT.is_active(at(6, 0)))
        self.assertFalse(OVERNIGHT.is_active(at(12, 0)))
        self.assertFalse(OVERNIGHT.is_active(at(21, 59, 59)))

    def test_equal_start_and_stop_means_all_day(self):
        settings = ReminderSettings(start=time(8, 0), stop=time(8, 0))
        self.assertTrue(all(settings.is_active(at(hour)) for hour in range(24)))

    def test_a_window_that_stops_at_midnight(self):
        settings = ReminderSettings(start=time(8, 0), stop=time(0, 0))
        self.assertTrue(settings.is_active(at(23, 59, 59)))
        self.assertFalse(settings.is_active(at(0, 0)))
        self.assertFalse(settings.is_active(at(7, 59)))
        self.assertTrue(settings.is_active(at(8, 0)))

    def test_next_open_is_the_moment_itself_when_the_window_is_open(self):
        self.assertEqual(ReminderSettings().next_open(at(10, 30)), at(10, 30))
        self.assertEqual(ReminderSettings().next_open(at(8, 0)), at(8, 0))

    def test_before_the_window_opens_it_opens_later_today(self):
        self.assertEqual(ReminderSettings().next_open(at(7, 0)), at(8, 0))
        self.assertEqual(ReminderSettings().next_open(at(0, 0)), at(8, 0))

    def test_after_the_window_closes_it_opens_tomorrow(self):
        self.assertEqual(ReminderSettings().next_open(at(22, 0)), at(8, 0, day=8))
        self.assertEqual(ReminderSettings().next_open(at(23, 59)), at(8, 0, day=8))

    def test_next_open_crosses_month_and_year_ends(self):
        self.assertEqual(ReminderSettings().next_open(datetime(2026, 11, 30, 23, 0)), datetime(2026, 12, 1, 8, 0))
        self.assertEqual(ReminderSettings().next_open(datetime(2026, 12, 31, 23, 0)), datetime(2027, 1, 1, 8, 0))

    def test_an_overnight_window_opens_this_evening(self):
        self.assertEqual(OVERNIGHT.next_open(at(12, 0)), at(22, 0))
        self.assertEqual(OVERNIGHT.next_open(at(6, 0)), at(22, 0))
        self.assertEqual(OVERNIGHT.next_open(at(23, 0)), at(23, 0))


class StartingTest(unittest.TestCase):
    def test_before_start_there_is_nothing_due(self):
        schedule = ReminderSchedule()
        self.assertIsNone(schedule.next_due)
        self.assertFalse(schedule.paused)
        self.assertFalse(schedule.poll(at(12, 0)))
        self.assertIsNone(schedule.seconds_until_due(at(12, 0)))

    def test_the_default_settings_are_used_when_none_are_given(self):
        self.assertEqual(ReminderSchedule().settings, ReminderSettings())
        custom = ReminderSettings(interval_minutes=20)
        self.assertIs(ReminderSchedule(custom).settings, custom)

    def test_inside_the_window_the_first_reminder_is_one_interval_away(self):
        schedule = ReminderSchedule()
        schedule.start(at(10, 0))
        self.assertEqual(schedule.next_due, at(10, 45))

    def test_before_the_window_the_first_reminder_is_when_it_opens(self):
        for hour, minute in ((3, 0), (7, 0), (7, 14), (7, 15)):
            schedule = ReminderSchedule()
            schedule.start(at(hour, minute))
            self.assertEqual(schedule.next_due, at(8, 0), f"started at {hour}:{minute:02d}")

    def test_in_the_evening_the_next_reminder_is_tomorrow_morning(self):
        for hour, minute, second in ((21, 30, 0), (21, 59, 59), (22, 0, 0), (23, 50, 0)):
            schedule = ReminderSchedule()
            schedule.start(at(hour, minute, second))
            self.assertEqual(schedule.next_due, at(8, 0, day=8), f"started at {hour}:{minute:02d}:{second:02d}")

    def test_an_overnight_window(self):
        schedule = ReminderSchedule(OVERNIGHT)
        schedule.start(at(23, 0))
        self.assertEqual(schedule.next_due, at(23, 45))
        schedule.start(at(12, 0))
        self.assertEqual(schedule.next_due, at(22, 0))
        schedule.start(at(5, 30))
        self.assertEqual(schedule.next_due, at(22, 0))

    def test_an_interval_of_a_whole_day_lands_on_the_same_time_tomorrow(self):
        schedule = ReminderSchedule(ReminderSettings(interval_minutes=24 * 60))
        schedule.start(at(10, 0))
        self.assertEqual(schedule.next_due, at(10, 0, day=8))

    def test_seconds_until_due(self):
        schedule = ReminderSchedule()
        schedule.start(at(10, 0))
        self.assertEqual(schedule.seconds_until_due(at(10, 30)), 15 * 60)
        self.assertEqual(schedule.seconds_until_due(at(11, 0)), 0.0)  # overdue is not negative


class PollingTest(unittest.TestCase):
    def test_nothing_happens_before_the_due_time_and_one_reminder_at_it(self):
        schedule = ReminderSchedule()
        schedule.start(at(10, 0))
        self.assertFalse(schedule.poll(at(10, 44, 59)))
        self.assertTrue(schedule.poll(at(10, 45)))
        self.assertEqual(schedule.next_due, at(11, 30))
        self.assertFalse(schedule.poll(at(10, 45)))  # the same moment again: already given

    def test_a_whole_day_of_reminders_follows_the_brief(self):
        schedule = ReminderSchedule()
        schedule.start(at(8, 0))
        given = []
        moment = at(8, 0)
        while moment <= at(10, 0, day=8):
            if schedule.poll(moment):
                given.append(moment)
            moment += timedelta(minutes=1)
        first_day = [at(8, 0) + timedelta(minutes=45 * k) for k in range(1, 19)]  # 08:45 ... 21:30
        self.assertEqual(first_day[0], at(8, 45))
        self.assertEqual(first_day[-1], at(21, 30))
        self.assertEqual(given, first_day + [at(8, 0, day=8), at(8, 45, day=8), at(9, 30, day=8)])
        self.assertTrue(all(ReminderSettings().is_active(moment) for moment in given))  # never outside 08:00-22:00

    def test_sleeping_through_several_reminders_gives_just_one(self):
        schedule = ReminderSchedule()
        schedule.start(at(10, 0))  # due 10:45, 11:30, 12:15, ... while the computer sleeps until 17:20
        self.assertTrue(schedule.poll(at(17, 20)))
        self.assertFalse(schedule.poll(at(17, 20)))
        self.assertEqual(schedule.next_due, at(18, 5))
        self.assertTrue(schedule.poll(at(18, 5)))

    def test_waking_up_after_the_window_closed_says_nothing_until_morning(self):
        schedule = ReminderSchedule()
        schedule.start(at(20, 0))  # due 20:45
        self.assertFalse(schedule.poll(at(23, 30)))
        self.assertEqual(schedule.next_due, at(8, 0, day=8))
        self.assertFalse(schedule.poll(at(7, 59, day=8)))
        self.assertTrue(schedule.poll(at(8, 0, day=8)))
        self.assertEqual(schedule.next_due, at(8, 45, day=8))

    def test_waking_up_very_late_but_inside_the_window_still_gives_one_reminder(self):
        schedule = ReminderSchedule()
        schedule.start(at(8, 0))
        self.assertTrue(schedule.poll(at(21, 59)))
        self.assertEqual(schedule.next_due, at(8, 0, day=8))


class SnoozePauseResumeTest(unittest.TestCase):
    def setUp(self):
        self.schedule = ReminderSchedule()
        self.schedule.start(at(10, 0))  # next reminder 10:45

    def test_snooze_means_remind_me_again_that_many_minutes_from_now(self):
        self.assertEqual(self.schedule.snooze(at(10, 30), 10), at(10, 40))
        self.assertEqual(self.schedule.next_due, at(10, 40))
        self.assertEqual(self.schedule.snooze(at(10, 30), 20), at(10, 50))  # later than the planned 10:45: postponed
        self.assertEqual(self.schedule.snooze(at(10, 1), 10), at(10, 11))  # earlier than the planned 10:45: "ask me again soon"

    def test_a_snoozed_reminder_is_given_then_the_normal_rhythm_goes_on(self):
        self.schedule.snooze(at(10, 30), 10)
        self.assertFalse(self.schedule.poll(at(10, 39, 59)))
        self.assertTrue(self.schedule.poll(at(10, 40)))
        self.assertEqual(self.schedule.next_due, at(11, 25))

    def test_a_snooze_that_ends_after_closing_time_waits_for_the_morning(self):
        self.assertEqual(self.schedule.snooze(at(21, 55), 10), at(8, 0, day=8))

    def test_nonsense_snooze_times_are_refused(self):
        for bad in (0, -1, float("nan"), True):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.schedule.snooze(at(10, 30), bad)
        self.assertEqual(self.schedule.next_due, at(10, 45))  # nothing changed

    def test_pausing_stops_all_reminders(self):
        self.schedule.pause()
        self.assertTrue(self.schedule.paused)
        self.assertIsNone(self.schedule.next_due)
        self.assertIsNone(self.schedule.seconds_until_due(at(12, 0)))
        self.assertFalse(self.schedule.poll(at(10, 45)))
        self.assertFalse(self.schedule.poll(at(20, 0)))

    def test_snoozing_while_paused_changes_nothing(self):
        self.schedule.pause()
        self.assertIsNone(self.schedule.snooze(at(11, 0), 10))
        self.assertTrue(self.schedule.paused)
        self.assertIsNone(self.schedule.next_due)

    def test_resuming_starts_a_fresh_interval(self):
        self.schedule.pause()
        self.schedule.resume(at(11, 0))
        self.assertFalse(self.schedule.paused)
        self.assertEqual(self.schedule.next_due, at(11, 45))
        self.assertTrue(self.schedule.poll(at(11, 45)))

    def test_resuming_in_the_evening_waits_for_the_morning(self):
        self.schedule.pause()
        self.schedule.resume(at(22, 30))
        self.assertEqual(self.schedule.next_due, at(8, 0, day=8))

    def test_reset_starts_the_interval_again_from_now(self):
        self.schedule.reset(at(10, 20))
        self.assertEqual(self.schedule.next_due, at(11, 5))

    def test_reset_does_not_end_a_pause(self):
        self.schedule.pause()
        self.schedule.reset(at(10, 20))
        self.assertTrue(self.schedule.paused)
        self.assertIsNone(self.schedule.next_due)

    def test_pausing_twice_is_harmless(self):
        self.schedule.pause()
        self.schedule.pause()
        self.assertTrue(self.schedule.paused)


class ChangingSettingsTest(unittest.TestCase):
    def test_a_new_interval_takes_effect_from_now(self):
        schedule = ReminderSchedule()
        schedule.start(at(10, 0))
        schedule.set_settings(ReminderSettings(interval_minutes=30), at(10, 10))
        self.assertEqual(schedule.settings.interval_minutes, 30)
        self.assertEqual(schedule.next_due, at(10, 40))

    def test_a_new_window_moves_the_next_reminder_into_it(self):
        schedule = ReminderSchedule()
        schedule.start(at(10, 0))  # 10:45
        schedule.set_settings(ReminderSettings(start=time(12, 0), stop=time(13, 0)), at(10, 0))
        self.assertEqual(schedule.next_due, at(12, 0))

    def test_a_paused_schedule_stays_paused(self):
        schedule = ReminderSchedule()
        schedule.start(at(10, 0))
        schedule.pause()
        schedule.set_settings(ReminderSettings(interval_minutes=30), at(10, 10))
        self.assertTrue(schedule.paused)
        self.assertIsNone(schedule.next_due)
        schedule.resume(at(10, 20))
        self.assertEqual(schedule.next_due, at(10, 50))  # and the new interval is used once it resumes

    def test_a_schedule_that_was_never_started_stays_unstarted(self):
        schedule = ReminderSchedule()
        schedule.set_settings(ReminderSettings(interval_minutes=30), at(10, 0))
        self.assertIsNone(schedule.next_due)


if __name__ == "__main__":
    unittest.main()
