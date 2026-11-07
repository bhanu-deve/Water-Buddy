"""The reminder entries of the menus: when the next reminder comes, snooze, pause, resume and "remind me now".

The same entries appear in the small menu that opens when you click the character, and (inside a "Reminders"
submenu, with "Remind me now" added) in the right-click menu. They are rebuilt every time a menu opens, so they
always show the current state.
"""

from __future__ import annotations

import math

from PySide6.QtWidgets import QMenu

from .reminder_scheduler import ReminderScheduler

SNOOZE_MINUTES = (10, 20)  # the choices from the project brief


def format_minutes(minutes: int) -> str:
    """23 -> '23 min', 65 -> '1 h 05 min', 120 -> '2 h'."""
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours} h" if rest == 0 else f"{hours} h {rest:02d} min"


def describe_next_reminder(scheduler: ReminderScheduler) -> str:
    """One line for the menus: when the next reminder comes (24-hour clock, like the Windows taskbar here)."""
    if scheduler.paused:
        return "Reminders are paused"
    due = scheduler.next_due
    if due is None:
        return "Reminders are off"
    now = scheduler.now()
    clock = f"{due:%H:%M}"
    days_ahead = (due.date() - now.date()).days
    if days_ahead <= 0:
        minutes = max(1, math.ceil((due - now).total_seconds() / 60))
        return f"Next reminder {clock} (in {format_minutes(minutes)})"
    if days_ahead == 1:
        return f"Next reminder tomorrow {clock}"
    return f"Next reminder {due:%d %b} {clock}"


def populate_reminder_menu(menu: QMenu, scheduler: ReminderScheduler, *, remind_now: bool = False) -> None:
    """Add the reminder entries to `menu`. With `remind_now`, a "Remind me now" entry (for trying things out) too."""
    info = menu.addAction(describe_next_reminder(scheduler))
    info.setEnabled(False)  # a line of information, not something to click

    for minutes in SNOOZE_MINUTES:
        snooze = menu.addAction(f"Snooze for {minutes} minutes")
        snooze.setEnabled(not scheduler.paused)  # nothing to postpone while everything is paused
        snooze.triggered.connect(lambda _checked=False, minutes=minutes: scheduler.snooze(minutes))

    pause = menu.addAction("Pause reminders")
    pause.setEnabled(not scheduler.paused)
    pause.triggered.connect(lambda _checked=False: scheduler.pause())

    resume = menu.addAction("Resume reminders")
    resume.setEnabled(scheduler.paused)
    resume.triggered.connect(lambda _checked=False: scheduler.resume())

    if remind_now:
        menu.addSeparator()
        now_action = menu.addAction("Remind me now")
        now_action.triggered.connect(lambda _checked=False: scheduler.remind_now())


def populate_reminders_submenu(menu: QMenu, scheduler: ReminderScheduler) -> None:
    """For the right-click menu: a "Reminders" submenu with everything, including "Remind me now"."""
    populate_reminder_menu(menu.addMenu("Reminders"), scheduler, remind_now=True)
