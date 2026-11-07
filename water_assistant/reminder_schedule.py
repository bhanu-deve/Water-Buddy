"""The rules of the water reminders - pure logic with no Qt and no real clock.

Every time here is a plain local date-time (what datetime.now() returns) that the caller hands in, so the rules can
be tested with made-up times and nothing in this file ever has to wait.

The rules:
  * Reminders come every `interval_minutes`, but only inside the daily window start..stop (default 08:00-22:00).
    `start` is included, `stop` is not. A window that ends before it starts (22:00-06:00) runs overnight, and
    start == stop means "all day".
  * The next reminder is always one interval after the moment the schedule was (re)started - at program start,
    after a reminder, after a snooze, after resume - and if that falls outside the window it is moved forward to the
    moment the window opens. So the first reminder of a day comes at the start time, and after the last one of the
    evening the next one is "tomorrow at the start time".
  * If the computer slept through a reminder, it is given once when the computer wakes up - not once per missed one.
  * Snooze means "remind me again N minutes from now", whatever was planned before.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, time, timedelta

DEFAULT_INTERVAL_MINUTES = 45.0
DEFAULT_START = time(8, 0)
DEFAULT_STOP = time(22, 0)
MIN_INTERVAL_MINUTES = 0.02  # 1.2 seconds: only for demos and tests (the settings window will start at 1 minute)
MAX_INTERVAL_MINUTES = 24 * 60.0


def _check_minutes(what: str, value: float, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{what} must be a number of minutes, not {value!r}")
    if not low <= value <= high:
        raise ValueError(f"{what} must be between {low:g} and {high:g} minutes, not {value:g}")
    return float(value)


@dataclass(frozen=True)
class ReminderSettings:
    """How often and when to remind. Immutable: to change something, make a new one."""

    interval_minutes: float = DEFAULT_INTERVAL_MINUTES
    start: time = DEFAULT_START
    stop: time = DEFAULT_STOP

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "interval_minutes",
            _check_minutes("the reminder interval", self.interval_minutes, MIN_INTERVAL_MINUTES, MAX_INTERVAL_MINUTES),
        )
        for label, value in (("start", self.start), ("stop", self.stop)):
            if not isinstance(value, time):
                raise ValueError(f"the {label} time must be a datetime.time, not {value!r}")

    @property
    def interval(self) -> timedelta:
        return timedelta(minutes=self.interval_minutes)

    def is_active(self, moment: datetime) -> bool:
        """Is the daily reminder window open at `moment`?"""
        clock = moment.time()
        if self.start == self.stop:
            return True  # all day
        if self.start < self.stop:
            return self.start <= clock < self.stop
        return clock >= self.start or clock < self.stop  # an overnight window, e.g. 22:00-06:00

    def next_open(self, moment: datetime) -> datetime:
        """`moment` itself if the window is open then, otherwise the next moment at which it opens."""
        if self.is_active(moment):
            return moment
        opening = datetime.combine(moment.date(), self.start)
        return opening if opening > moment else opening + timedelta(days=1)


class ReminderSchedule:
    """Knows when the next reminder is due. It never looks at a clock: every method is told what time it is."""

    def __init__(self, settings: ReminderSettings | None = None) -> None:
        self._settings = settings or ReminderSettings()
        self._next_due: datetime | None = None  # None while paused, and before start()
        self._paused = False

    # ------------------------------------------------------------ information
    @property
    def settings(self) -> ReminderSettings:
        return self._settings

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def next_due(self) -> datetime | None:
        """When the next reminder is due (None while paused or not started)."""
        return self._next_due

    def seconds_until_due(self, now: datetime) -> float | None:
        return None if self._next_due is None else max(0.0, (self._next_due - now).total_seconds())

    # -------------------------------------------------------------- changing it
    def _after(self, now: datetime, delay: timedelta) -> datetime:
        return self._settings.next_open(now + delay)

    def start(self, now: datetime) -> None:
        """(Re)start the schedule: the first reminder is one interval from now (or when the window opens)."""
        self._paused = False
        self._next_due = self._after(now, self._settings.interval)

    def reset(self, now: datetime) -> None:
        """Start counting the interval again from now (a reminder was just given, or you just drank). No effect while paused."""
        if not self._paused:
            self._next_due = self._after(now, self._settings.interval)

    def snooze(self, now: datetime, minutes: float) -> datetime | None:
        """Remind again `minutes` from now. Returns the new due time, or None (nothing changes) while paused."""
        minutes = _check_minutes("the snooze time", minutes, MIN_INTERVAL_MINUTES, MAX_INTERVAL_MINUTES)
        if self._paused:
            return None
        self._next_due = self._after(now, timedelta(minutes=minutes))
        return self._next_due

    def pause(self) -> None:
        self._paused = True
        self._next_due = None

    def resume(self, now: datetime) -> None:
        """Remind again, starting a fresh interval from now."""
        self.start(now)

    def set_settings(self, settings: ReminderSettings, now: datetime) -> None:
        """Use new settings. A running schedule starts a fresh interval; a paused or unstarted one stays as it is."""
        self._settings = settings
        if not self._paused and self._next_due is not None:
            self.start(now)

    # ---------------------------------------------------------------- the check
    def poll(self, now: datetime) -> bool:
        """True if a reminder must be given right now (and the next one is planned); otherwise False."""
        if self._paused or self._next_due is None or now < self._next_due:
            return False
        if self._settings.is_active(now):
            self._next_due = self._after(now, self._settings.interval)
            return True
        # Woke up outside the window (for example after a night's sleep): nothing to say until it opens.
        self._next_due = self._settings.next_open(now)
        return False
