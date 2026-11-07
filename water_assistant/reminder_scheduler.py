"""Gives the reminders at the right moment: a Qt timer around the pure rules in reminder_schedule.py.

The timer never sleeps longer than MAX_SLEEP_SECONDS. Instead of trusting one long timer for 45 minutes, it wakes up
every few seconds, looks at the real clock and asks the rules "is a reminder due?". That way a computer that slept
(or whose clock was changed) is noticed within seconds, and waiting costs next to nothing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from .reminder_schedule import ReminderSchedule, ReminderSettings


class ReminderScheduler(QObject):
    reminderDue = Signal()  # time to remind: show the character, say a message
    stateChanged = Signal()  # the next reminder time or the paused state changed (menus, tray and dashboard refresh)

    MAX_SLEEP_SECONDS = 20.0
    MIN_SLEEP_SECONDS = 0.05  # also stops a timer that fires a hair too early from spinning

    def __init__(
        self,
        settings: ReminderSettings | None = None,
        *,
        clock: Callable[[], datetime] = datetime.now,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._clock = clock  # tests hand in a fake clock
        self._schedule = ReminderSchedule(settings)
        self._running = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timer)

    # ------------------------------------------------------------ information
    @property
    def settings(self) -> ReminderSettings:
        return self._schedule.settings

    @property
    def paused(self) -> bool:
        return self._schedule.paused

    @property
    def running(self) -> bool:
        return self._running

    @property
    def next_due(self) -> datetime | None:
        """When the next reminder is due (None while paused or not started)."""
        return self._schedule.next_due

    def now(self) -> datetime:
        return self._clock()

    # --------------------------------------------------------------- lifecycle
    def start(self) -> None:
        """Begin: the first reminder is one interval from now (or when the daily window opens)."""
        self._running = True
        self._schedule.start(self._clock())
        self._changed()

    def stop(self) -> None:
        """Stop for good (the program is exiting). No more reminders."""
        self._running = False
        self._timer.stop()

    # ---------------------------------------------------------------- control
    def set_settings(self, settings: ReminderSettings) -> None:
        self._schedule.set_settings(settings, self._clock())
        self._changed()

    def snooze(self, minutes: float) -> datetime | None:
        """Remind again `minutes` from now. Returns when, or None while paused."""
        due = self._schedule.snooze(self._clock(), minutes)
        self._changed()
        return due

    def pause(self) -> None:
        self._schedule.pause()
        self._changed()

    def resume(self) -> None:
        self._schedule.resume(self._clock())
        self._changed()

    def reset(self) -> None:
        """Count the interval again from now (for example because you just drank water)."""
        self._schedule.reset(self._clock())
        self._changed()

    def remind_now(self) -> None:
        """Give a reminder right now, by hand. The normal interval starts again from now (a pause stays a pause)."""
        if self._running:
            self._schedule.reset(self._clock())
        self.reminderDue.emit()
        self._changed()

    # --------------------------------------------------------------- the timer
    def _changed(self) -> None:
        self._arm()
        self.stateChanged.emit()

    def _arm(self) -> None:
        """Set the timer to wake up when the next reminder is due (or after MAX_SLEEP_SECONDS, whichever is sooner)."""
        seconds = self._schedule.seconds_until_due(self._clock()) if self._running else None
        if seconds is None:  # not running, or paused: no wake-ups at all
            self._timer.stop()
            return
        delay = min(max(seconds, self.MIN_SLEEP_SECONDS), self.MAX_SLEEP_SECONDS)
        self._timer.start(int(delay * 1000) + 5)

    def _on_timer(self) -> None:
        if not self._running:  # stop() was called: a late timer event must not give a reminder
            return
        before = self._schedule.next_due
        due = self._schedule.poll(self._clock())
        if due:
            self.reminderDue.emit()
        self._arm()  # after the signal: a slot may have snoozed or paused meanwhile
        if due or self._schedule.next_due != before:
            self.stateChanged.emit()
