"""Application start-up: creates the Qt application, the character and its voice, and wires them together."""

from __future__ import annotations

import argparse
import logging
import re
import signal
import sys
from datetime import time

from PySide6.QtWidgets import QApplication, QMessageBox

from .animation_controller import AnimationController
from .character_window import CharacterWindow
from .paths import asset_path
from .reminder_menu import populate_reminder_menu, populate_reminders_submenu
from .reminder_schedule import MAX_INTERVAL_MINUTES, MIN_INTERVAL_MINUTES, ReminderSettings
from .reminder_scheduler import ReminderScheduler
from .speech_bubble import SpeechBubble
from .speech_service import SpeechService
from .sprites import CharacterSprites, SpriteLoadError
from .voice_menu import populate_voice_menu

APP_NAME = "Water Assistant"


def _percent(text: str) -> int:
    value = int(text)
    if not 0 <= value <= 100:
        raise argparse.ArgumentTypeError("must be between 0 and 100")
    return value


def _minutes(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a number of minutes") from None
    if not MIN_INTERVAL_MINUTES <= value <= MAX_INTERVAL_MINUTES:  # also false for "nan"
        raise argparse.ArgumentTypeError(f"must be between {MIN_INTERVAL_MINUTES:g} and {MAX_INTERVAL_MINUTES:g}")
    return value


def _hours(text: str) -> tuple[time, time]:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})", text.strip())
    if match is None:
        raise argparse.ArgumentTypeError("must look like 08:00-22:00 (24-hour clock; 00:00-00:00 means all day)")
    start_hour, start_minute, stop_hour, stop_minute = (int(part) for part in match.groups())
    try:
        return time(start_hour, start_minute), time(stop_hour, stop_minute)
    except ValueError:
        raise argparse.ArgumentTypeError("hours must be 0-23 and minutes 0-59") from None


def reminder_settings(options: argparse.Namespace) -> ReminderSettings:
    """The reminder settings: the defaults from the brief (every 45 minutes, 08:00-22:00), unless options change them."""
    changes = {}
    if options.reminder_interval is not None:
        changes["interval_minutes"] = options.reminder_interval
    if options.reminder_hours is not None:
        changes["start"], changes["stop"] = options.reminder_hours
    return ReminderSettings(**changes)


def parse_arguments(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    """The program's command-line options. Anything unknown is handed on to Qt, which has options of its own."""
    parser = argparse.ArgumentParser(prog="main.py", description="Your animated water-drinking assistant.")
    parser.add_argument(
        "--no-voice",
        action="store_true",
        help="start with speaking switched off (the speech bubble still appears; the right-click menu can switch it on)",
    )
    parser.add_argument(
        "--volume",
        type=_percent,
        metavar="0-100",
        help="speaking volume in percent; 0 is silent, but the voice still 'speaks' (the test tools use this)",
    )
    parser.add_argument(
        "--reminder-interval",
        type=_minutes,
        metavar="MINUTES",
        help="minutes between reminders (default 45). To see reminders quickly: 0.5 means every 30 seconds",
    )
    parser.add_argument(
        "--reminder-hours",
        type=_hours,
        metavar="START-STOP",
        help="the hours in which reminders are given (default 08:00-22:00, 24-hour clock); 00:00-00:00 means all day",
    )
    return parser.parse_known_args(argv)


def main(argv: list[str] | None = None) -> int:
    options, qt_arguments = parse_arguments(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")  # problems show in the terminal

    # Let Ctrl+C in the terminal stop the app (Qt's event loop would otherwise ignore it).
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    app = QApplication([sys.argv[0], *qt_arguments])
    app.setApplicationName(APP_NAME)

    try:
        sprites = CharacterSprites(asset_path("character", "water_reminder.gif"))
    except SpriteLoadError as error:
        # A packaged .exe has no console, so show the problem in a dialog instead of failing silently.
        QMessageBox.critical(None, APP_NAME, str(error))
        return 1

    speech = SpeechService(
        enabled=not options.no_voice,
        volume=SpeechService.DEFAULT_VOLUME if options.volume is None else options.volume / 100,
    )
    window = CharacterWindow(sprites)
    bubble = SpeechBubble()
    controller = AnimationController(window, bubble, speech=speech)
    reminders = ReminderScheduler(reminder_settings(options))

    reminders.reminderDue.connect(controller.remind)  # hop, sign, speech bubble and voice (shows the character if hidden)
    window.add_menu_section(lambda menu: populate_reminders_submenu(menu, reminders))  # right-click menu
    window.add_menu_section(lambda menu: populate_voice_menu(menu, speech, controller.say))
    window.add_quick_menu_section(lambda menu: populate_reminder_menu(menu, reminders), order=20)  # click menu

    window.exitRequested.connect(app.quit)
    app.aboutToQuit.connect(controller.stop)
    app.aboutToQuit.connect(reminders.stop)
    app.aboutToQuit.connect(speech.shutdown)  # ends the speech thread cleanly, even in the middle of a sentence

    speech.start()  # the voice warms up in the background while the window appears
    window.move_to_bottom_right()
    window.show()
    controller.start()  # says hello (aloud, if the voice is on), then idles and strolls around from time to time
    reminders.start()  # the first reminder comes one interval from now (or at 08:00 if that is still hours away)
    return app.exec()
