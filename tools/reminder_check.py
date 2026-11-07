r"""Real-desktop check of the Phase 4 reminders (re-run after every phase).

Run:   .\.venv\Scripts\python.exe tools\reminder_check.py

WARNING: takes about 2 minutes and MOVES YOUR MOUSE and clicks (real mouse input). Don't touch the mouse or the
keyboard while it runs. The voice is the real Windows speech engine but at volume 0, so it is silent.

Reminders are scheduled every 12 seconds, all day (so the check works at any time of day), and it proves:
  A. a scheduled reminder arrives on time: the character hops, shows the bubble with the FIRST message, the voice
     says it, and the bubble stays until the voice has finished
  C. a real LEFT click on the character opens the small menu (and does not nudge the character); the reminder
     controls are there; "Snooze for 10 minutes" really postpones a reminder that was due in seconds
  D. "Pause reminders" really stops them, the menu then offers "Resume reminders" (Pause and Snooze greyed out), and
     after "Resume reminders" the next one arrives one interval later with the SECOND message (messages rotate)
  E. a reminder that comes due while you hold the character with the mouse waits until you let go
  F. a reminder shows the character if it was hidden, without taking the keyboard focus from the program in use
  G. "Exit" in the small menu asks the program to exit

SAFETY: before any click is injected, the window under that point must belong to THIS process (the character, the
bubble or the program's own menu); otherwise the script aborts without clicking. Screenshots go to
%TEMP%\water_assistant_checks.
"""
import re
import sys
import time
from datetime import time as clock_time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import winharness as h  # noqa: E402  (this folder is on sys.path because the script is run from it)
from water_assistant import messages  # noqa: E402
from water_assistant.animation_controller import AnimationController, State  # noqa: E402
from water_assistant.character_window import CharacterWindow  # noqa: E402
from water_assistant.paths import asset_path  # noqa: E402
from water_assistant.reminder_menu import populate_reminder_menu, populate_reminders_submenu  # noqa: E402
from water_assistant.reminder_schedule import ReminderSettings  # noqa: E402
from water_assistant.reminder_scheduler import ReminderScheduler  # noqa: E402
from water_assistant.speech_bubble import SpeechBubble  # noqa: E402
from water_assistant.speech_service import SpeechService  # noqa: E402
from water_assistant.sprites import CharacterSprites  # noqa: E402

INTERVAL_SECONDS = 12.0

app = QApplication(sys.argv)
h.start_session()

# ------------------------------------------------------------------------------------------ the program, as in app.py
sprites = CharacterSprites(asset_path("character", "water_reminder.gif"))
window = CharacterWindow(sprites)
bubble = SpeechBubble()
speech = SpeechService(volume=0.0)  # the REAL voice, silent
speech.set_rate(225)  # a little faster, so that a reminder is over well before the next one is due
controller = AnimationController(window, bubble, speech=speech)
reminders = ReminderScheduler(
    ReminderSettings(interval_minutes=INTERVAL_SECONDS / 60, start=clock_time(0, 0), stop=clock_time(0, 0))  # all day
)
reminders.reminderDue.connect(controller.remind)
window.add_menu_section(lambda menu: populate_reminders_submenu(menu, reminders))
window.add_quick_menu_section(lambda menu: populate_reminder_menu(menu, reminders), order=20)
window.set_wandering_enabled(False)  # keep the run deterministic
exits = []
window.exitRequested.connect(lambda: exits.append(1))
h.on_exit(lambda: (reminders.stop(), speech.shutdown(), bubble.close(), window.close()))

arrivals = []  # when each reminder came due
reminders.reminderDue.connect(lambda: arrivals.append(time.monotonic()))
speech_events = []  # (kind, time)
speech.utteranceStarted.connect(lambda _uid: speech_events.append(("started", time.monotonic())))
speech.utteranceFinished.connect(lambda _uid, _ok: speech_events.append(("finished", time.monotonic())))

speech.start()
window.move_to_bottom_right()
window.show()
h.pump(0.5)
controller.start(greet=False)
hwnd = int(window.winId())
dpr = window.devicePixelRatioF()
h.check("the real Windows speech engine is ready", h.wait_until(lambda: speech.voices, 20))
h.pump(2.5)  # its silent warm-up


# ------------------------------------------------------------------------------------------ helpers
def character_centre():
    left, top, right, bottom = h.rect_of(hwnd)
    return (left + right) // 2, (top + bottom) // 2


def menu_entries(menu):
    return [(action.text(), action.isEnabled()) for action in menu.actions() if not action.isSeparator()]


def use_small_menu(handler):
    """Open the small menu with a REAL left click on the character. `handler` runs while the menu is open and must
    close it (by clicking an entry, or menu.close()). This call returns when the menu has closed."""
    x, y = character_centre()
    h.safe_target(x, y, "the character")
    QTimer.singleShot(700, handler)
    h.move_cursor(x, y)
    h.press(h.LEFTDOWN)
    h.press(h.LEFTUP)  # the click: blocks here while the menu is open


def click_entry(text, record):
    """A handler for use_small_menu: note what the menu shows, then click the entry `text` with the real mouse."""

    def handler():
        menu = QApplication.activePopupWidget()
        record["found"] = menu is not None
        if menu is None:
            return
        record["entries"] = menu_entries(menu)
        record["menu"] = menu
        h.snap(f"R_menu_{text.split()[0].lower()}", int(menu.winId()), hwnd, margin=20)
        x, y = h.center_of(menu, text, dpr)
        h.safe_target(x, y, f"menu entry '{text}'")
        record["clicked_at"] = time.monotonic()
        h.move_cursor(x, y, wait=False)
        h.press(h.LEFTDOWN, wait=False)
        h.press(h.LEFTUP, wait=False)

    return handler


def reminder_text(count):
    """Wait until reminder number `count` reaches the talking stage; return the bubble text (or None)."""
    ok = h.wait_until(lambda: len(arrivals) >= count and controller.state is State.TALKING and bubble.isVisible(), 12)
    return bubble.text if ok else None


def wait_for_calm(timeout=20):
    """Wait until the character is idle (the reminder is over) and has landed again."""
    return h.wait_until(lambda: controller.state is State.IDLE and not bubble.isVisible(), timeout)


base_top = h.rect_of(hwnd)[1]  # where the character stands on the ground

# ------------------------------------------------------------------------------------------ A. the first scheduled reminder
tops = []
sampler = QTimer()
sampler.setInterval(20)
sampler.timeout.connect(lambda: tops.append(h.rect_of(hwnd)[1]))
reminders.start()
started_at = time.monotonic()
sampler.start()
arrived = h.wait_until(lambda: len(arrivals) >= 1, INTERVAL_SECONDS + 5)
elapsed = arrivals[0] - started_at if arrivals else 0.0
h.check("A: the first reminder arrives one interval (12 s) after the start", arrived and INTERVAL_SECONDS - 0.7 <= elapsed <= INTERVAL_SECONDS + 0.8,
        f"after {elapsed:.2f} s")
h.check("A: the character begins the reminder (hop and sign)", controller.state is State.REMINDER)
first_text = reminder_text(1)
sampler.stop()
h.check("A: it hops", base_top - min(tops) >= 12, f"rose {base_top - min(tops)} px")
h.check("A: then the bubble shows the FIRST reminder message", first_text == messages.REMINDER_MESSAGES[0], repr(first_text))
h.snap("R_A_reminder", hwnd, int(bubble.winId()))
h.check("A: the real voice starts saying it", h.wait_until(lambda: any(kind == "started" for kind, _t in speech_events), 6))
h.check("A: the bubble stays up while the voice is speaking", controller.state is State.TALKING and bubble.isVisible()
        and not any(kind == "finished" for kind, _t in speech_events))
h.check("A: afterwards the character calms down and stands on the ground again", wait_for_calm() and h.rect_of(hwnd)[1] == base_top)

# ------------------------------------------------------------------------------------------ C. the small menu and Snooze
fg_before = h.foreground()
before = h.rect_of(hwnd)
record = {}
use_small_menu(click_entry("Snooze for 10 minutes", record))
h.pump(0.3)
entries = record.get("entries", [])
h.check("C: a real click on the character opens the small menu", record.get("found"))
h.check("C: ...and a click does not nudge the character", h.rect_of(hwnd) == before)
first_line = entries[0][0] if entries else ""
h.check("C: the first line tells when the next reminder comes (greyed out)",
        bool(re.fullmatch(r"Next reminder \d\d:\d\d \(in (\d+ min|\d+ h( \d\d min)?)\)", first_line)) and not entries[0][1], first_line)
h.check("C: the menu has Snooze 10 / 20, Pause, Resume and Exit as in the brief",
        [text for text, _ in entries[1:]] == ["Snooze for 10 minutes", "Snooze for 20 minutes", "Pause reminders", "Resume reminders", "Exit"], str(entries))
h.check("C: Resume is greyed out while the reminders are running",
        dict(entries).get("Resume reminders") is False and dict(entries).get("Pause reminders") is True)
due_in = (reminders.next_due - reminders.now()).total_seconds() if reminders.next_due else -1
h.check("C: 'Snooze for 10 minutes' (real click) moved the next reminder to 10 minutes from now", abs(due_in - 600) < 15, f"due in {due_in:.0f} s")
h.check("C: the click and its menu did not take the keyboard focus away", h.wait_until(lambda: h.foreground() == fg_before, 1.5),
        f"in front before: {h.describe_window(fg_before)}; after: {h.describe_window(h.foreground())}")
arrivals_before = len(arrivals)
h.check("C: the reminder that was due in a few seconds really did not come (waited 14 s)",
        not h.wait_until(lambda: len(arrivals) > arrivals_before, INTERVAL_SECONDS + 2))

# ------------------------------------------------------------------------------------------ D. Pause and Resume
record = {}
use_small_menu(click_entry("Pause reminders", record))
h.pump(0.3)
h.check("D: 'Pause reminders' (real click) pauses the reminders", reminders.paused)
arrivals_before = len(arrivals)
h.check("D: nothing comes while paused (waited 14 s, longer than the interval)",
        not h.wait_until(lambda: len(arrivals) > arrivals_before, INTERVAL_SECONDS + 2))
record = {}
use_small_menu(click_entry("Resume reminders", record))
h.pump(0.3)
h.check("D: while paused the menu says so, and offers only Resume",
        record.get("entries") == [("Reminders are paused", False), ("Snooze for 10 minutes", False), ("Snooze for 20 minutes", False),
                                  ("Pause reminders", False), ("Resume reminders", True), ("Exit", True)], str(record.get("entries")))
h.check("D: 'Resume reminders' (real click) resumes them", not reminders.paused)
arrived = h.wait_until(lambda: len(arrivals) > arrivals_before, INTERVAL_SECONDS + 5)
since_resume = arrivals[-1] - record["clicked_at"] if arrived else 0.0
h.check("D: after resuming, the next reminder comes one interval later", arrived and INTERVAL_SECONDS - 1.0 <= since_resume <= INTERVAL_SECONDS + 1.0,
        f"{since_resume:.2f} s after the click")
second_text = reminder_text(len(arrivals))
h.check("D: ...with the SECOND message (the messages rotate)", second_text == messages.REMINDER_MESSAGES[1], repr(second_text))
h.check("D: and the reminder ends normally", wait_for_calm())

# ------------------------------------------------------------------------------------------ E. a reminder waits while you hold the character
x, y = character_centre()
h.safe_target(x, y, "the character (to hold it)")
count_before = len(arrivals)
h.move_cursor(x, y)
h.press(h.LEFTDOWN)  # the mouse button goes down on the character and STAYS down
for step in range(1, 7):  # a real drag: far enough to be a drag, not a click
    h.move_cursor(x - 8 * step, y - 5 * step)
held_tops, held_states, held_bubble = set(), set(), []
hold_until = time.monotonic() + 3 * INTERVAL_SECONDS
while time.monotonic() < hold_until:  # hold until a reminder has come due (and a little longer)
    h.pump(0.02)
    if len(arrivals) > count_before and time.monotonic() - arrivals[-1] > 2.0:
        break
    held_tops.add(h.rect_of(hwnd)[1])
    held_states.add(controller.state)
    held_bubble.append(bubble.isVisible())
came_due_while_held = len(arrivals) > count_before
h.check("E: a reminder came due while the mouse button was held down", came_due_while_held)
h.check("E: ...but the character did not hop out from under the mouse (its height never changed)", len(held_tops) == 1, f"{len(held_tops)} different heights")
h.check("E: ...nor show the reminder (still idle, no bubble)", held_states == {State.IDLE} and not any(held_bubble), str(held_states))
h.press(h.LEFTUP)  # let go: it was a drag, so no menu opens
h.check("E: the moment the mouse button is released the waiting reminder starts", h.wait_until(lambda: controller.state is State.REMINDER, 2.0))
third_text = reminder_text(len(arrivals))
h.check("E: ...with the THIRD message", third_text == messages.REMINDER_MESSAGES[2], repr(third_text))
h.check("E: and it ends normally", wait_for_calm())

# ------------------------------------------------------------------------------------------ F. a reminder shows a hidden character
fg_before = h.foreground()
window.hide()
h.pump(0.3)
h.check("F: the character is hidden", not window.isVisible())
reminders.remind_now()
h.pump(0.3)
h.check("F: a reminder shows the hidden character", window.isVisible() and controller.state is State.REMINDER)
h.check("F: ...without taking the keyboard focus from the program in use", h.foreground() == fg_before,
        f"in front before: {h.describe_window(fg_before)}; after: {h.describe_window(h.foreground())}")
fourth_text = reminder_text(len(arrivals))
h.check("F: ...with the FOURTH message", fourth_text == messages.REMINDER_MESSAGES[3], repr(fourth_text))
h.check("F: and it ends normally", wait_for_calm())

# ------------------------------------------------------------------------------------------ G. Exit in the small menu
record = {}
use_small_menu(click_entry("Exit", record))
h.pump(0.3)
h.check("G: 'Exit' in the small menu (real click) asks the program to exit", exits == [1])

sys.exit(h.finish())  # (the mouse is put back where it was when the tool ends)
