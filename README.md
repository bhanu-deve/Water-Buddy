# Water Assistant

Your own animated cartoon character that floats on the Windows desktop, reminds you to drink
water, talks to you and tracks your daily intake. Python + PySide6 + Pillow + pyttsx3, fully offline.

## Status

| Phase | Content | State |
|---|---|---|
| 1 | Desktop character: transparent floating window, drag, resize, always on top | **done, tested** |
| 2 | Animations: GIF states, smooth walking, speech bubbles | **done, tested** |
| 3 | Offline voice (pyttsx3 / Windows SAPI5) | **done, tested** (confirm by ear with `tools\voice_check.py`) |
| 4 | Reminder engine: interval, hours, snooze, pause, click menu | **done, tested** |
| 5 | Water tracking (SQLite) | next |
| 6 | Dashboard, settings, system tray | |
| 7 | Windows packaging (PyInstaller `.exe`) | |

## Setup (once)

In a PowerShell terminal opened in this folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

You never need to "activate" the environment: always call `.\.venv\Scripts\python.exe`
directly (this also avoids PowerShell's script-execution restrictions).

Keep the project in a folder with a **short path** (like `Documents\Water`). Windows limits paths to 260 characters
and PySide6 contains deeply nested files, so in a very deep folder `pip install` fails with
"No such file or directory ... PySide6\qml\Qt\labs\..." (seen while testing a fresh setup).

## Run

```powershell
.\.venv\Scripts\python.exe main.py
```

Quit: right-click the character -> **Exit** (or press Ctrl+C in the terminal).

Options (all optional):

| Option | Meaning |
|---|---|
| `--no-voice` | start with speaking switched off (the speech bubble still appears; the menu can switch it on) |
| `--volume 0-100` | speaking volume in percent. `0` is silent, but the voice still "speaks" (the test tools use this) |
| `--reminder-interval MINUTES` | minutes between reminders (default 45). `0.5` = every 30 seconds, for a quick try-out |
| `--reminder-hours START-STOP` | the hours in which reminders are given (default `08:00-22:00`, 24-hour clock). `00:00-00:00` = all day |

To see reminders quickly at any time of day (outside 08:00-22:00 the next reminder is "tomorrow 08:00"):

```powershell
.\.venv\Scripts\python.exe main.py --reminder-interval 0.5 --reminder-hours 00:00-00:00
```

## What the character does

| State | What you see and hear |
|---|---|
| IDLE | stands, breathes and sways gently |
| WALKING | strolls across the screen on its own (first stroll ~7 s after the greeting, then every 25-60 s) |
| REMINDER | hops twice, points at you, the "Drink Water!" sign blinks, then **says the reminder aloud** |
| TALKING | speech bubble above its head, gesturing poses, and the sentence **spoken aloud** (a greeting at start-up) |
| DRINKING | drinks from the bottle |
| CELEBRATING | thumbs-up, then the wink with the "Stay Hydrated!" banner, two happy hops (a message is spoken too) |

Right-click the character -> **Preview animation** to trigger any of them right now, including
**Play the original GIF** (all 8 frames in the GIF's own timing). **Walk around on its own** switches the
strolling off. These menu entries are for trying things out; the real triggers arrive in Phases 4-5.

Other controls: **drag** with the left mouse button (a walk stops the moment you grab it), **resize** with the
mouse wheel or Size menu, **Move to bottom-right corner**, **Exit**.

Behaviour you can rely on: transparent parts of the picture and the whole speech bubble are click-through,
so other apps stay fully usable; the character never takes keyboard focus away from the app you are typing in.

## Reminders (Phase 4)

The rules (all in `reminder_schedule.py`, plain logic that is tested with made-up clock times):

* A reminder every **45 minutes**, but only between **08:00 and 22:00** (start included, stop not). Both are options
  now (`--reminder-interval`, `--reminder-hours`) and become real settings in Phase 6.
* The timer starts when the program starts: the first reminder is one interval later. If that moment is outside the
  hours, it is moved to when they open - so the first reminder of a day comes at **08:00**, and after the last one of
  the evening the next is "tomorrow 08:00". (Right after midnight, for example, nothing comes until morning.)
* If the PC sleeps through a reminder you get **one** when it wakes up, not a pile of missed ones.
* **Snooze for 10 / 20 minutes** means "remind me again that many minutes from now", whatever was planned before
  (so after a reminder it asks again sooner, and before one it postpones). **Pause** stops everything; **Resume**
  starts a fresh interval. Messages rotate through 8 sentences (the 4 from your brief first). The brief's "Good job!
  Keep drinking water regularly." is praise, so it is saved for after you drink water (Phase 5).
* When a reminder comes due the character **appears if it was hidden**, hops, points with the "Drink Water!" sign and
  says the message in the bubble and aloud. If you are **holding the character with the mouse or have its menu open,
  the reminder waits** until you let go - the character never hops out from under your mouse.

Menus:

* **Click the character** (a press and release without moving more than 5 pixels; moving further is a drag) -> the
  small menu from your brief: when the next reminder comes, Snooze for 10 minutes, Snooze for 20 minutes,
  Pause reminders, Resume reminders, Exit. (Drink 250 ml / 500 ml / Custom amount arrive in Phase 5,
  Settings / Today's progress in Phase 6.)
* **Right-click** -> *Reminders* has the same entries plus **Remind me now** (to try the reminder at once).
* The first line of the small menu is information (greyed out); Pause / Resume / Snooze are greyed out when they do
  not apply.

Not done yet: **Hide / Show** entries - a hidden character needs the system-tray icon of Phase 6 to be brought back
(a reminder already shows a hidden character, and that is tested). Reminder settings are not saved between runs yet
(Phase 6), and drinking water will restart the interval in Phase 5 (`ReminderScheduler.reset()` is ready for it).

## Voice (Phase 3)

Right-click the character -> **Voice**:

| Entry | What it does |
|---|---|
| Speak aloud | switches the voice on / off (the bubble always shows the text) |
| Test voice | the character says "Hi! This is how I sound. Stay hydrated!" |
| Stop speaking | cuts the current sentence short |
| Speed | Slow / Normal / Fast (120 / 165 / 225 words per minute) |
| Volume | Quiet 40 % / Medium 70 % (default) / Loud 100 % |
| Voice | every Windows voice installed on the computer; the default is the first English male voice |

* This PC has two voices: **Microsoft David Desktop** (male, the default) and **Microsoft Zira Desktop** (female).
  More can be added in Windows Settings -> Time & language -> Speech; they appear in the menu by themselves.
* These choices are **not remembered between runs yet** (Phase 6 adds saved settings).
* The voice is fully offline (Windows' built-in SAPI5 engine). If no voice can be started, the character simply
  stays silent, the menu says "Voice not available on this computer", and the reason is printed in the terminal.
* The bubble stays up until the voice has finished plus a short pause (0.7 s), never less than the time needed to
  read it. Starting anything else (a walk, a drink, a new sentence) silences the old sentence. A safety net
  continues after 25 s if the voice ever fails to report back.
* Windows only has speed steps of about 11 %, so the three presets are spaced widely. Measured with one sentence:
  Slow 4.65 s, Normal 3.84 s, Fast 2.80 s.

How it works, briefly: speaking takes seconds, so the engine lives on a **worker thread** of its own (Windows speech
objects must be used by the thread that created them). The rest of the program sends it sentences through a queue
and hears back through Qt signals that always arrive on the main thread, so the screen never freezes while talking.

### Three pitfalls found in pyttsx3 2.99 (each has a test in `tests\test_speech_real.py`)

1. `engine.runAndWait()` **hangs forever** when called while the engine is idle (it queues its own "end loop"
   command, which runs before the loop starts). -> only the "external loop" (`startLoop(False)` + `iterate()`) is used.
2. `engine.stop()` leaves a **stale "end of speech" event** behind. If the next sentence has already started, that
   sentence is reported as finished after a fraction of a second (measured: 0.2 s, 2 of 8 words). -> after every
   cancelled sentence the engine is replaced by a fresh one (about half a second).
3. Cancelling before the engine has begun a sentence produces **no "finished" event at all**, so a loop waiting for
   one never ends. -> cancellation is checked before starting, and every wait has a time limit.

Also: the first sentence of a new engine starts about a second late (the voice has to be loaded), so a silent
one-word warm-up is spoken while the program is idle (measured: first sentence 4.31 s -> 3.82 s).

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest -v              # 276 tests, ~95 s, silent, no windows, no mouse
.\.venv\Scripts\python.exe tools\desktop_check.py      # real desktop, ~20 s, MOVES YOUR MOUSE (24 checks)
.\.venv\Scripts\python.exe tools\launch_check.py       # real main.py: a scheduled reminder, both menus, Exit; ~30 s, MOVES YOUR MOUSE (13 checks)
.\.venv\Scripts\python.exe tools\animation_check.py    # animations + voice menu on the real desktop, ~2 min, MOVES YOUR MOUSE (42 checks)
.\.venv\Scripts\python.exe tools\reminder_check.py     # reminders, click menu, snooze, pause, hold, hidden; ~2.5 min, MOVES YOUR MOUSE (35 checks)
.\.venv\Scripts\python.exe tools\voice_check.py        # MAKES SOUND for ~20 s; measures it; never touches the mouse
```

Re-run all of them after every phase: they catch regressions in always-on-top, click-through, focus, smooth
walking, the speech bubble, the voice and the reminders. The `desktop`, `launch`, `animation` and `reminder` tools
only ever click on windows that belong to the test itself (re-checked right before each click), and write screenshots
to `%TEMP%\water_assistant_checks`. `tools\winharness.py` holds their shared helpers (older tools still carry a copy).

Leave the mouse and keyboard alone while those four run. If another window comes to the front, a script
stops early on purpose ("ABORT: ... not over a window of this test") instead of clicking someone else's window -
just run it again.

About the voice in the tests: they use the **real** Windows speech engine but make no sound - speech is played at
volume 0 (`launch_check` and `animation_check` start the program that way) or rendered into WAV files that are then
analysed. `voice_check.py` proves that this is really silent: it reads the peak meter of this program's own audio
session in Windows (other programs' sounds cannot confuse it) - 0.000 at volume 0, clearly above zero when audible.
To skip the slower real-engine unit tests, run `$env:WATER_ASSISTANT_NO_REAL_SPEECH = "1"` first.

## Layout

```
main.py                       entry point
water_assistant/
    app.py                    command-line options, starts Qt, wires everything together
    paths.py                  finds assets (also inside a PyInstaller .exe)
    sprites.py                GIF -> separate frames (Pillow), feet aligned -> sharp pixmaps (Qt)
    poses.py                  which GIF frame is which pose + the frame sequences (clips)
    character_window.py       the floating transparent window (draws a frame + squash/lean, reports user actions)
    animation_controller.py   the six states, smooth movement (QVariantAnimation), frame timing, wandering,
                              keeping the bubble up until the voice has finished
    speech_bubble.py          the click-through speech bubble window
    messages.py               what the character says (greeting, reminders, congratulations)
    speech_backend.py         the real Windows speech engine (pyttsx3 + SAPI5) behind a small interface
    speech_service.py         worker thread + queue + Qt signals; speed / volume / voice / on-off; stop()
    voice_menu.py             the "Voice" entry of the right-click menu
    reminder_schedule.py      the reminder rules (interval, hours, snooze, pause) - pure logic, no Qt, no real clock
    reminder_scheduler.py     the Qt timer around those rules; says "reminder due"; never sleeps more than 20 s
    reminder_menu.py          the reminder entries of the small click menu and of the right-click menu
assets/character/water_reminder.gif   your animation (copy of the original GIF; the original is untouched)
tests/    tests (tests/fakes.py: a silent fake speech engine and a hand-moved fake clock)
tools/    real-desktop checks (see above)
```

Planned modules (one per later phase): water tracking service, database, settings, dashboard, system tray.

## Artwork notes

`water_reminder.gif`: 8 frames, 382x510 px, 0.56 s each, real transparency (no background removal needed).
The frame meanings live in [water_assistant/poses.py](water_assistant/poses.py).

| Frame | Pose | Used for |
|---|---|---|
| 0 | standing, holding the bottle | IDLE and the pause between steps |
| 1 | mid-step, right foot forward | WALKING |
| 2 | pointing at the viewer, open smile | REMINDER, TALKING |
| 3 | mid-step, left foot forward, bottle raised | WALKING, TALKING |
| 4 | drinking from the bottle | DRINKING |
| 5 | thumbs-up | CELEBRATING |
| 6 | pointing at a "Drink Water!" bubble (text is part of the art) | REMINDER |
| 7 | winking, "Stay Hydrated!" banner (text is part of the art) | CELEBRATING |

Things to know:

* **The walk is a front-facing stepping cycle** (right foot, stand, left foot, stand, plus a bounce and a slight
  lean), because the GIF only has "walking toward the camera" poses. On screen the character therefore moves
  sideways while stepping. A truly natural walk needs new artwork: a side-view walk cycle (about 6-8 frames,
  facing left and right, same character). Then only `poses.py` needs to change.
* **Feet are aligned**: frames 2, 4, 5 and 6 sat 3-11 px away from the others' ground line, so they are shifted by
  whole pixels (nothing is redrawn or resized) to stop the feet jumping when the pose changes.
* **Breathing, sway, bounce and lean** are programmatic motion added on top of your frames, not new artwork.
* `original_avatar.png` was not provided; nothing depends on it.

## Performance (measured on a 1080p laptop at 150 % scaling, as a share of ONE CPU core)

| State | CPU |
|---|---|
| Idle (breathing, ~7 redraws per second), voice engine ready | about 1.4-1.7 % |
| Talking with the speech bubble (20 redraws per second), animation only | about 4.7 % |
| Speech engine speaking a sentence (on top of the animation) | about 3.6-3.9 % |
| Speech engine ready but idle | about 0.01 % |
| Drinking | about 2.4 % |
| Walking (60 window moves + 30 redraws per second; a few seconds per stroll) | about 19 % (12 % of that is just moving the window) |

Moving any window 60 times a second costs about that much on this PC - a plain opaque window costs the same - so it is
not specific to this app. Each redraw of the character costs about 2.5 ms of CPU, which is why redraws are kept low
when the motion is slow.

How this was measured: with Windows' exact cycle counter (`QueryProcessCycleTime`, see `tools/animation_check.py`).
Python's `time.process_time()` is NOT suitable: Windows only samples it every ~15 ms, and for short periodic work it
gave readings anywhere from 0.2 % to 5 % for the same code. A measuring loop that wakes up 100 times a second also
distorts the result - wait the way the program does (Qt's event loop sleeps).

## Notes for later phases

* **Packaging (Phase 7):** pyttsx3 loads its Windows driver by name while running, which PyInstaller cannot see, so a
  hidden import of `pyttsx3.drivers.sapi5` (and of comtypes' generated speech module) will probably be needed. To be
  verified when packaging; the `.exe` must then be tested with the voice, not just started.
* **Saved settings (Phase 6):** the voice choices (on/off, speed, volume, voice) are plain properties of
  `SpeechService` (`set_enabled`, `set_rate`, `set_volume`, `set_voice`), ready to be loaded and saved.
