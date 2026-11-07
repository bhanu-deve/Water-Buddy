r"""Real-desktop check of the Phase 2 animations (re-run after every phase).

Run:   .\.venv\Scripts\python.exe tools\animation_check.py

WARNING: takes ~1 minute. For part of that time it MOVES YOUR MOUSE and clicks (real mouse input), and a
bright green helper window appears briefly. Don't touch the mouse while it runs.

What it proves on the real Windows desktop (the unit tests cannot):
  * idle: the character really redraws (breathing) and that costs almost no CPU
  * walking at real speed is smooth: many intermediate positions, ~60 updates per second, constant height,
    right speed, only the step frames, ends exactly on the target
  * the reminder: hop, "Drink Water!" sign, then the speech bubble - without stealing keyboard focus
  * the speech bubble is a topmost, click-through overlay that stays on the screen (a real click on it
    reaches the application underneath)
  * a real mouse drag during a walk stops the walk and the character follows the mouse
  * the new "Preview animation" menu works with the real mouse (right-click -> submenu -> Drink)

SAFETY: before any click is injected, the window under that point must belong to THIS process
(the character, the bubble, the green helper or the menu); otherwise the script aborts without clicking.
Screenshots are written to  %TEMP%\water_assistant_checks.
"""
import atexit
import ctypes
import os
import statistics
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
OUT = Path(tempfile.gettempdir()) / "water_assistant_checks"
OUT.mkdir(exist_ok=True)
sys.path.insert(0, str(PROJECT))

from PIL import ImageGrab  # noqa: E402
from PySide6.QtCore import QEvent, QEventLoop, QObject, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from water_assistant.animation_controller import AnimationController, State  # noqa: E402
from water_assistant.character_window import CharacterWindow  # noqa: E402
from water_assistant.paths import asset_path  # noqa: E402
from water_assistant.poses import Pose  # noqa: E402
from water_assistant.speech_bubble import SpeechBubble  # noqa: E402
from water_assistant.speech_service import SpeechService  # noqa: E402
from water_assistant.sprites import CharacterSprites  # noqa: E402
from water_assistant.voice_menu import TEST_SENTENCE, populate_voice_menu  # noqa: E402

# ------------------------------------------------------------------ Win32 plumbing
user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]

GWL_EXSTYLE = -20
WS_EX_TOPMOST, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW = 0x8, 0x20, 0x80
WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x80000, 0x08000000
LEFTDOWN, LEFTUP, RIGHTDOWN, RIGHTUP, MOVE_ABS = 0x2, 0x4, 0x8, 0x10, 0x8001

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"   ({detail})" if detail else ""), flush=True)


app = QApplication(sys.argv)
SCREEN_W, SCREEN_H = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)  # real pixels (Qt makes us DPI aware)


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


# CPU use is measured with Windows' exact cycle counter. (time.process_time() only samples every ~15 ms, which
# for short periodic work can be off by a factor of several in either direction - see the README.)
kernel32 = ctypes.WinDLL("kernel32")
kernel32.GetCurrentProcess.restype = wintypes.HANDLE
kernel32.QueryProcessCycleTime.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_ulonglong)]


def process_cycles():
    value = ctypes.c_ulonglong()
    kernel32.QueryProcessCycleTime(kernel32.GetCurrentProcess(), ctypes.byref(value))
    return value.value


_c0, _t0 = process_cycles(), time.perf_counter()
while time.perf_counter() - _t0 < 1.0:  # a busy loop uses exactly 100 % of one core: that calibrates cycles -> time
    pass
CYCLES_PER_CORE_SECOND = (process_cycles() - _c0) / (time.perf_counter() - _t0)


def cpu_percent_since(cycles_before, seconds_before):
    """Percent of ONE CPU core used since the given moment."""
    return (process_cycles() - cycles_before) / ((time.perf_counter() - seconds_before) * CYCLES_PER_CORE_SECOND) * 100


def run_for(seconds):
    """Run Qt's real event loop (it sleeps properly, so CPU measurements are honest)."""
    loop = QEventLoop()
    QTimer.singleShot(int(seconds * 1000), loop.quit)
    loop.exec()


def wait_until(condition, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if condition():
            return True
        pump(0.01)
    return bool(condition())


def pid_of(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def rect_of(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def window_at(x, y):
    return user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))


def describe_window(hwnd):
    """Which application a window belongs to. Class name only: window titles can contain private text."""
    if not hwnd:
        return "no window"
    cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls, 256)
    owner = "this test" if pid_of(hwnd) == os.getpid() else f"another app (pid {pid_of(hwnd)})"
    return f"{owner}, class {cls.value!r}"


def safe_target(x, y, label):
    hwnd = window_at(x, y)
    if not hwnd or pid_of(hwnd) != os.getpid():
        raise SystemExit(
            f"ABORT: '{label}' point ({x},{y}) is not over a window of this test - refusing to inject input "
            f"(a foreign window is there: {describe_window(hwnd)})"
        )
    return hwnd


def move_cursor(x, y, wait=True):
    nx, ny = round(x * 65535 / (SCREEN_W - 1)), round(y * 65535 / (SCREEN_H - 1))
    user32.mouse_event(MOVE_ABS, nx, ny, 0, 0)
    pump(0.04) if wait else time.sleep(0.05)


def press(flag, wait=True):
    user32.mouse_event(flag, 0, 0, 0, 0)
    pump(0.04) if wait else time.sleep(0.05)


def grab():
    return ImageGrab.grab(include_layered_windows=True).convert("RGB")


def snap(name, *hwnds, margin=30):
    """Save a screenshot cropped around the given windows."""
    rects = [rect_of(h) for h in hwnds]
    box = (
        max(0, min(r[0] for r in rects) - margin),
        max(0, min(r[1] for r in rects) - margin),
        min(SCREEN_W, max(r[2] for r in rects) + margin),
        min(SCREEN_H, max(r[3] for r in rects) + margin),
    )
    grab().crop(box).save(OUT / f"{name}.png")


class PaintCounter(QObject):
    """Counts how often a window is repainted."""

    def __init__(self):
        super().__init__()
        self.count = 0

    def eventFilter(self, _watched, event):  # noqa: N802
        if event.type() == QEvent.Type.Paint:
            self.count += 1
        return False


class Helper(QWidget):
    """An ordinary application window that sits UNDER the bubble and records clicks.

    It is "always on top" and never takes focus, on purpose. Without that, a new window opened by a background
    program can end up BEHIND the window you are working in when you use the PC while the test runs (that made
    two earlier runs abort with VS Code in front of the helper). Being topmost keeps it above every normal window;
    the test then raises the bubble above it, so a click on the bubble must pass THROUGH the bubble to reach it.
    """

    def __init__(self):
        super().__init__()
        self.presses = []
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("Water Assistant animation check - ordinary window under the bubble")
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(0, 255, 0))
        self.setPalette(palette)

    def mousePressEvent(self, event):  # noqa: N802
        self.presses.append(event.globalPosition().toPoint())


# ------------------------------------------------------------------------- run
cursor0 = wintypes.POINT()
user32.GetCursorPos(ctypes.byref(cursor0))
opened_windows = []  # everything this script shows, so it can be closed even if the script aborts


speech = None  # created below; restore_everything() must work even if the script stops before that


def restore_everything():
    user32.SetCursorPos(cursor0.x, cursor0.y)
    if speech is not None:
        speech.shutdown()
    for widget in opened_windows:
        widget.close()


atexit.register(restore_everything)

sprites = CharacterSprites(asset_path("character", "water_reminder.gif"))
window = CharacterWindow(sprites)
bubble = SpeechBubble()
# The REAL Windows speech engine, at volume 0: it genuinely "speaks" (takes real time, reports back) but is silent.
speech = SpeechService(volume=0.0)
controller = AnimationController(window, bubble, speech=speech)  # real speed, no shortcuts
window.add_menu_section(lambda menu: populate_voice_menu(menu, speech, controller.say))
opened_windows += [window, bubble]
window.set_wandering_enabled(False)  # keep the run deterministic
window.move_to_bottom_right()
window.show()
pump(0.5)
speech.start()
controller.start(greet=False)
hwnd = int(window.winId())
dpr = window.devicePixelRatioF()
print(f"screen real={SCREEN_W}x{SCREEN_H}  device-pixel-ratio={dpr}  character logical size={window.width()}x{window.height()}")
check("voice: the real Windows speech engine starts and lists its voices", wait_until(lambda: speech.voices, 20),
      f"{len(speech.voices)} voice(s): " + ", ".join(voice.name.split(' - ')[0] for voice in speech.voices))
pump(2.5)  # its silent warm-up runs now, so it does not disturb the idle CPU measurement below

# ---- A. idle: it really moves, and that is cheap
cycles0, wall0 = process_cycles(), time.perf_counter()
run_for(8.0)  # measured BEFORE any measuring instruments are installed, so it is honest
idle_cpu = cpu_percent_since(cycles0, wall0)
counter = PaintCounter()
window.installEventFilter(counter)
run_for(3.0)
window.removeEventFilter(counter)
repaints_per_second = counter.count / 3.0
check("idle: the breathing is redrawn about 7 times per second", 4 <= repaints_per_second <= 10, f"{repaints_per_second:.1f} repaints/s")
images = set()
for _ in range(16):
    images.add(window.grab().toImage().constBits().tobytes())
    pump(0.25)
check("idle: breathing really changes the picture over time", len(images) >= 4, f"{len(images)} different pictures in 4 s")
check("idle: costs very little CPU", idle_cpu < 4.0, f"{idle_cpu:.2f} % of one core")
snap("A_idle", hwnd)

# ---- B. walking at real speed
left_limit, right_limit = controller._walk_limits()
start_geo = window.geometry()
target = max(left_limit, start_geo.x() - 400)
distance = start_geo.x() - target
expected_seconds = distance / (controller._timing.walk_speed * window.scale)
move_times, samples = [], []  # (time, left, top, frame)


def note_move_time():
    move_times.append(time.perf_counter())


window.geometryChanged.connect(note_move_time)
sampler = QTimer()
sampler.setInterval(5)
loop = QEventLoop()
walk_started = [0.0]
sampler.timeout.connect(lambda: samples.append((time.perf_counter() - walk_started[0], *rect_of(hwnd)[:2], window.frame_index)))
controller.stateChanged.connect(lambda state: loop.quit() if state is State.IDLE else None)
walk_started[0] = time.perf_counter()
controller.walk_to(target)
sampler.start()
QTimer.singleShot(20000, loop.quit)
shot_timer = QTimer()
shot_timer.setSingleShot(True)
shot_timer.timeout.connect(lambda: snap("B_walking_midway", hwnd))
shot_timer.start(int(expected_seconds * 500))  # a screenshot halfway through the walk
loop.exec()
sampler.stop()
window.geometryChanged.disconnect(note_move_time)  # only ours: the controller keeps its own connection

lefts = [s[1] for s in samples]
tops = {s[2] for s in samples}
seconds = samples[-1][0] - samples[0][0]
intervals = [(b - a) * 1000 for a, b in zip(move_times, move_times[1:])][5:-5]  # skip the ramp ends
check("walking: arrives exactly on the target", abs(lefts[-1] - round(target * dpr)) <= 1, f"final x={lefts[-1]} px, wanted {round(target * dpr)} px")
check("walking: speed is right", abs(seconds - expected_seconds) < 0.35, f"{distance} px took {seconds:.2f} s, expected {expected_seconds:.2f} s")
check("walking: only ever heads towards the target (no stepping back)", lefts == sorted(lefts, reverse=True))
check("walking: stays on the ground line (height never changes)", len(tops) == 1, f"{len(tops)} different heights")
check("walking: many intermediate positions (smooth, not jumpy)", len(set(lefts)) >= 150, f"{len(set(lefts))} different positions")
check("walking: ~60 position updates per second", statistics.median(intervals) < 20 and sorted(intervals)[int(len(intervals) * 0.95)] < 45,
      f"median {statistics.median(intervals):.1f} ms, 95th percentile {sorted(intervals)[int(len(intervals) * 0.95)]:.1f} ms between updates")
frames_seen = {s[3] for s in samples}
check("walking: uses only the step frames of the GIF", frames_seen <= {Pose.STEP_RIGHT, Pose.STAND, Pose.STEP_LEFT} and len(frames_seen) == 3, f"frames {sorted(frames_seen)}")
runs, current, started = [], samples[0][3], samples[0][0]
for t, _l, _t, frame in samples[1:]:
    if frame != current:
        runs.append(t - started)
        current, started = frame, t
check("walking: one step pose every ~0.26 s", abs(statistics.median(runs[1:-1]) - 0.26) < 0.05, f"median {statistics.median(runs[1:-1]):.3f} s")
check("walking: back to idle with the standing pose", controller.state is State.IDLE and window.frame_index == Pose.STAND)

# CPU while walking, measured in a second walk back to the start with no instruments running.
# Moving ANY window 60 times a second costs roughly 10-15 % of one core on this PC (measured with a plain
# opaque window too), and a stroll only lasts a few seconds every minute or so.
cycles0, wall0 = process_cycles(), time.perf_counter()
controller.walk_to(start_geo.x())
QTimer.singleShot(20000, loop.quit)
loop.exec()
walk_cpu = cpu_percent_since(cycles0, wall0)
check("walking: CPU cost is moderate and only lasts for the stroll", walk_cpu < 35.0, f"{walk_cpu:.1f} % of one core while walking")
check("walking: back at the start position", abs(window.x() - start_geo.x()) <= 1 and controller.state is State.IDLE)

# ---- C. reminder + speech bubble (no focus stealing)
fg_before = user32.GetForegroundWindow()
base_pos = window.pos()
message = "Hey! It's time to drink some water."
heights = []


def note_height():
    heights.append(window.y())


window.geometryChanged.connect(note_height)
controller.remind(message)
saw_sign = wait_until(lambda: window.frame_index == Pose.SIGN_DRINK_WATER, 3)
pump(0.12)
snap("C_reminder_sign", hwnd)
wait_until(lambda: controller.state is State.TALKING, 6)
window.geometryChanged.disconnect(note_height)
pump(0.5)  # let the bubble fade in
hop_height = base_pos.y() - min(heights)
check("reminder: the character hops", hop_height >= 8, f"jumped {hop_height} logical px")
check("reminder: the 'Drink Water!' sign frame is shown", saw_sign)
check("reminder: then the speech bubble appears with the message", bubble.isVisible() and bubble.text == message)
check("reminder: the character landed back exactly where it started", window.pos() == base_pos)
bubble_hwnd = int(bubble.winId())
bex = user32.GetWindowLongW(bubble_hwnd, GWL_EXSTYLE)
check("bubble: topmost, layered, tool window, never activates", all(bex & flag for flag in (WS_EX_TOPMOST, WS_EX_LAYERED, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE)))
check("bubble: click-through window style (WS_EX_TRANSPARENT)", bex & WS_EX_TRANSPARENT)
bl, bt, br, bb = rect_of(bubble_hwnd)
cl, ct, cr, cb = rect_of(hwnd)
pad = round(bubble.MARGIN * dpr)  # the window has an invisible margin around the visible bubble (outline + shadow)
check("bubble: the visible bubble is completely on the screen", bl + pad >= 0 and bt + pad >= 0 and br - pad <= SCREEN_W and bb - pad <= SCREEN_H,
      f"window rect=({bl},{bt},{br},{bb}) screen={SCREEN_W}x{SCREEN_H}, invisible margin {pad} px")
check("bubble: sits above the character, tail pointing at its head", bubble.tail_points_down and bb <= ct + round(bubble.MARGIN * dpr) + 1)
fg_after = user32.GetForegroundWindow()
check("reminder: did not steal keyboard focus from the active window", fg_after == fg_before,
      f"in front before: {describe_window(fg_before)}; after: {describe_window(fg_after)}")
snap("C_talking_with_bubble", hwnd, bubble_hwnd)

# ---- D. a REAL click on the bubble must reach the application underneath
controller.say("Don't forget to stay hydrated!", seconds=60)
pump(0.6)
bubble_hwnd = int(bubble.winId())
bl, bt, br, bb = rect_of(bubble_hwnd)
bx, by = (bl + br) // 2, (bt + bb) // 2 - round(6 * dpr)
helper = Helper()
opened_windows.append(helper)
helper.setGeometry(int(bl / dpr) - 30, int(bt / dpr) - 30, int((br - bl) / dpr) + 60, int((bb - bt) / dpr) + 20)
helper.show(); pump(0.4)
bubble.raise_()  # the bubble must be ABOVE the helper, otherwise the helper would simply catch the click
pump(0.5)
inset = 3  # logical px: stay clear of the bubble's outline
body_height = bubble.height() - 2 * bubble.MARGIN - bubble.TAIL_HEIGHT  # the bubble is above the character: body at the top
body_box = (round(bl + (bubble.MARGIN + inset) * dpr), round(bt + (bubble.MARGIN + inset) * dpr),
            round(br - (bubble.MARGIN + inset) * dpr), round(bt + (bubble.MARGIN + body_height - inset) * dpr))
body = grab().crop(body_box)
pixels = body.tobytes()  # RGB triples
white = sum(1 for i in range(0, len(pixels), 3) if min(pixels[i], pixels[i + 1], pixels[i + 2]) > 235)
white_share = white / (body.width * body.height)
check("bubble: is drawn ABOVE the ordinary green window (so a click has to pass through it)", white_share > 0.5,
      f"{white_share:.0%} of the bubble body shows its white fill")
check("bubble: Windows hit-test sees THROUGH it", window_at(bx, by) != bubble_hwnd)
safe_target(bx, by, "centre of the bubble")
move_cursor(bx, by); press(LEFTDOWN); press(LEFTUP)
check("bubble: a real click on it reaches the app underneath", len(helper.presses) == 1, f"helper received {len(helper.presses)} click(s)")
snap("D_bubble_over_ordinary_window", bubble_hwnd, hwnd)
helper.close(); pump(0.2)
controller.go_idle(); pump(0.4)

# ---- E. a REAL drag during a walk stops the walk and the character follows the mouse
left_limit, right_limit = controller._walk_limits()
controller.walk_to(left_limit)
wait_until(lambda: window.x() < right_limit - 150, 5)
pump(0.1)
l, t, r, b = rect_of(hwnd)
gx, gy = (l + r) // 2, (t + b) // 2
safe_target(gx, gy, "walking character")
move_cursor(gx, gy)
press(LEFTDOWN)
check("drag: grabbing the walking character stops the walk at once", controller.state is State.IDLE)
before = window.pos()
for i in range(1, 9):
    move_cursor(gx - 12 * i, gy - 8 * i)
press(LEFTUP)
moved = window.pos() - before
check("drag: the character follows the mouse", abs(moved.x() + 96 / dpr) <= 3 and abs(moved.y() + 64 / dpr) <= 3, f"moved {moved.x()},{moved.y()} logical px")
settled = window.pos()
pump(1.5)
check("drag: it stays where it was dropped (no walk resumes, no snapping back)", window.pos() == settled)

# ---- F. the Preview menu, with the real mouse: right-click -> "Preview animation" -> "Drink"
window.move_to_bottom_right(); pump(0.4)
l, t, r, b = rect_of(hwnd)
px, py = (l + r) // 2, (t + b) // 2
safe_target(px, py, "right-click target")
state = {}


def center_of(menu, text):
    action = next(a for a in menu.actions() if a.text() == text)
    c = menu.mapToGlobal(menu.actionGeometry(action).center())
    return round(c.x() * dpr), round(c.y() * dpr)


def hover_preview_entry():
    menu = QApplication.activePopupWidget()
    state["menu"] = menu is not None
    if menu is None:
        return
    x, y = center_of(menu, "Preview animation")
    safe_target(x, y, "Preview animation entry")
    move_cursor(x, y, wait=False)  # hovering opens the submenu
    QTimer.singleShot(900, click_drink)


def click_drink():
    submenu = QApplication.activePopupWidget()
    state["submenu"] = submenu is not None and any(a.text() == "Drink" for a in submenu.actions())
    if not state["submenu"]:
        return
    x, y = center_of(submenu, "Drink")
    safe_target(x, y, "Drink entry")
    snap("F_preview_submenu", int(submenu.winId()), hwnd, margin=20)
    move_cursor(x, y, wait=False); press(LEFTDOWN, wait=False); press(LEFTUP, wait=False)


QTimer.singleShot(900, hover_preview_entry)
move_cursor(px, py); press(RIGHTDOWN); press(RIGHTUP)  # blocks here until the menu has closed
check("menu: right-click opens the menu and hovering 'Preview animation' opens its submenu", state.get("menu") and state.get("submenu"))
check("menu: clicking 'Drink' starts the drinking animation", controller.state is State.DRINKING and window.frame_index == Pose.DRINK)
pump(0.5)
snap("F_drinking", hwnd)
check("menu: ...which continues into the celebration with a speech bubble", wait_until(lambda: controller.state is State.CELEBRATING, 6) and bubble.isVisible())
pump(1.2)
snap("F_celebrating", hwnd, bubble_hwnd if bubble.isVisible() else hwnd)
check("menu: ...and finally returns to idle", wait_until(lambda: controller.state is State.IDLE, 8))

# ---- G. the Voice menu, with the real mouse and the real speech engine: right-click -> "Voice" -> "Test voice"
speech_times = {}
speech.utteranceStarted.connect(lambda _uid: speech_times.setdefault("started", time.monotonic()))
speech.utteranceFinished.connect(lambda _uid, _ok: speech_times.setdefault("finished", time.monotonic()))
window.move_to_bottom_right(); pump(0.4)
l, t, r, b = rect_of(hwnd)
px, py = (l + r) // 2, (t + b) // 2
safe_target(px, py, "right-click target (voice menu)")
voice_state = {}


def hover_voice_entry():
    menu = QApplication.activePopupWidget()
    voice_state["menu"] = menu is not None
    if menu is None:
        return
    x, y = center_of(menu, "Voice")
    safe_target(x, y, "Voice entry")
    move_cursor(x, y, wait=False)  # hovering opens the submenu
    QTimer.singleShot(900, click_test_voice)


def click_test_voice():
    submenu = QApplication.activePopupWidget()
    voice_state["labels"] = [a.text() for a in submenu.actions() if not a.isSeparator()] if submenu is not None else None
    if submenu is None or not any(a.text() == "Test voice" for a in submenu.actions()):
        return
    x, y = center_of(submenu, "Test voice")
    safe_target(x, y, "Test voice entry")
    snap("G_voice_submenu", int(submenu.winId()), hwnd, margin=20)
    move_cursor(x, y, wait=False); press(LEFTDOWN, wait=False); press(LEFTUP, wait=False)


QTimer.singleShot(900, hover_voice_entry)
clicked_at = time.monotonic()
move_cursor(px, py); press(RIGHTDOWN); press(RIGHTUP)  # blocks here until the menu has closed
check("voice menu: hovering 'Voice' opens its submenu with all the controls",
      voice_state.get("labels") == ["Speak aloud", "Test voice", "Stop speaking", "Speed", "Volume", "Voice"], str(voice_state.get("labels")))
check("voice menu: 'Test voice' makes the character say the test sentence in its bubble",
      wait_until(lambda: controller.state is State.TALKING and bubble.text == TEST_SENTENCE and bubble.isVisible(), 4))
check("voice menu: the real speech engine starts speaking it", wait_until(lambda: "started" in speech_times, 10))
snap("G_talking", hwnd, int(bubble.winId()))
check("voice: the bubble stays while the voice is speaking", controller.state is State.TALKING and "finished" not in speech_times)
check("voice: the voice finishes by itself after realistic time", wait_until(lambda: "finished" in speech_times, 15)
      and 1.2 < speech_times["finished"] - speech_times["started"] < 12,
      f"spoke for {speech_times.get('finished', 0) - speech_times.get('started', 0):.2f} s")
went_idle = [None]
controller.stateChanged.connect(lambda state: went_idle.__setitem__(0, time.monotonic()) if state is State.IDLE and went_idle[0] is None else None)
check("voice: the character goes back to idle after the voice, not before", wait_until(lambda: controller.state is State.IDLE, 6))
pause = (went_idle[0] or 0) - speech_times.get("finished", 0)
check("voice: ...following the voice by the short pause (about 0.7 s)", 0.4 <= pause <= 2.5, f"{pause:.2f} s after the voice ended")
check("voice: the bubble is gone afterwards", wait_until(lambda: not bubble.isVisible(), 3))

# ---- cleanup
user32.SetCursorPos(cursor0.x, cursor0.y)
controller.stop(); speech.shutdown(); bubble.close(); window.close(); pump(0.2)
passed = sum(results)
print(f"\n{passed}/{len(results)} checks passed   (screenshots: {OUT})")
sys.exit(0 if passed == len(results) else 1)
