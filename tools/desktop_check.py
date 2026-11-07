r"""Real-desktop check of the character window (Phase 1 behaviour; re-run after every phase).

Run:   .\.venv\Scripts\python.exe tools\desktop_check.py

WARNING: this takes ~20 seconds and MOVES YOUR MOUSE and clicks (real mouse input). Don't touch the
mouse while it runs. A bright green helper window appears briefly behind the character.

What it proves on the real Windows desktop (the unit tests cannot):
  * window styles: always-on-top, per-pixel transparent, no taskbar button, never steals focus
  * the character is drawn pixel-accurately and the transparent area shows the desktop
  * transparent pixels are click-through (a click there reaches the app underneath)
  * the character stays above a window opened after it
  * a real mouse drag moves it, a real wheel notch resizes it, a real right-click opens the menu
  * a real click on "Exit" asks the application to exit

SAFETY: before any click is injected, the window under that point must belong to THIS process
(the character, the green helper or the menu); otherwise the script aborts without clicking.
Screenshots are written to  %TEMP%\water_assistant_checks.
"""
import ctypes
import os
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
OUT = Path(tempfile.gettempdir()) / "water_assistant_checks"
OUT.mkdir(exist_ok=True)
sys.path.insert(0, str(PROJECT))

from PIL import Image, ImageChops, ImageGrab, ImageStat  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from water_assistant.character_window import CharacterWindow  # noqa: E402
from water_assistant.paths import asset_path  # noqa: E402
from water_assistant.sprites import CharacterSprites  # noqa: E402

# ------------------------------------------------------------------ Win32 plumbing
user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]

GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_CAPTION, WS_THICKFRAME = 0x00C00000, 0x00040000
WS_EX_TOPMOST, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW = 0x8, 0x20, 0x80
WS_EX_APPWINDOW, WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x40000, 0x80000, 0x08000000
LEFTDOWN, LEFTUP, RIGHTDOWN, RIGHTUP, WHEEL, MOVE_ABS = 0x2, 0x4, 0x8, 0x10, 0x800, 0x8001

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"   ({detail})" if detail else ""), flush=True)


app = QApplication(sys.argv)
SCREEN_W, SCREEN_H = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)  # real pixels (Qt makes us DPI aware)


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


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


def safe_target(x, y, label):
    hwnd = window_at(x, y)
    if not hwnd or pid_of(hwnd) != os.getpid():
        raise SystemExit(f"ABORT: '{label}' point ({x},{y}) is not over a window of this test - refusing to inject input")
    return hwnd


def move_cursor(x, y, wait=True):
    nx, ny = round(x * 65535 / (SCREEN_W - 1)), round(y * 65535 / (SCREEN_H - 1))
    user32.mouse_event(MOVE_ABS, nx, ny, 0, 0)
    pump(0.04) if wait else time.sleep(0.05)


def press(flag, wait=True):
    user32.mouse_event(flag, 0, 0, 0, 0)
    pump(0.04) if wait else time.sleep(0.05)


def wheel_notch(n):
    user32.mouse_event(WHEEL, 0, 0, (120 * n) & 0xFFFFFFFF, 0)
    pump(0.2)


def grab():
    return ImageGrab.grab(include_layered_windows=True).convert("RGB")


def qimage_to_pil(qimage):
    qimage = qimage.convertToFormat(QImage.Format.Format_RGBA8888)
    return Image.frombuffer("RGBA", (qimage.width(), qimage.height()), bytes(qimage.constBits()), "raw", "RGBA", qimage.bytesPerLine(), 1)


def masked_mean_diff(a, b, mask_value, alpha):
    """Mean |a-b| over the pixels whose sprite alpha equals mask_value (255 = opaque, 0 = transparent)."""
    mask = alpha.point(lambda v, m=mask_value: 255 if v == m else 0)
    stat = ImageStat.Stat(ImageChops.difference(a.convert("RGB"), b.convert("RGB")), mask=mask)
    return sum(stat.mean) / 3


class Helper(QWidget):
    """An ordinary application window that sits UNDER the character and records clicks."""

    def __init__(self):
        super().__init__()
        self.presses = []
        self.setWindowTitle("Water Assistant desktop check - ordinary window under the character")
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(0, 255, 0))
        self.setPalette(palette)

    def mousePressEvent(self, event):
        self.presses.append(event.globalPosition().toPoint())


# ------------------------------------------------------------------------- run
cursor0 = wintypes.POINT()
user32.GetCursorPos(ctypes.byref(cursor0))

sprites = CharacterSprites(asset_path("character", "water_reminder.gif"))
window = CharacterWindow(sprites)
exits = []
window.exitRequested.connect(lambda: exits.append(1))
window.move_to_bottom_right()
window.show()
pump(0.7)

hwnd = int(window.winId())
dpr = window.devicePixelRatioF()
screen = app.primaryScreen()
print(f"Qt platform={app.platformName()}  screen={screen.name()}  logical={screen.geometry().width()}x{screen.geometry().height()}  "
      f"real={SCREEN_W}x{SCREEN_H}  device-pixel-ratio={dpr}")
print(f"character logical geometry={window.geometry().getRect()}  real rect={rect_of(hwnd)}")

# ---- 1. window styles
style, ex = user32.GetWindowLongW(hwnd, GWL_STYLE), user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
check("window is TOPMOST (always on top)", ex & WS_EX_TOPMOST)
check("window is layered (per-pixel transparency)", ex & WS_EX_LAYERED)
check("window is a tool window (no taskbar button, not APPWINDOW)", (ex & WS_EX_TOOLWINDOW) and not (ex & WS_EX_APPWINDOW))
check("window does not activate on click (no focus stealing style)", ex & WS_EX_NOACTIVATE)
check("window is NOT click-through as a whole (WS_EX_TRANSPARENT absent)", not (ex & WS_EX_TRANSPARENT))
check("window has no title bar / border / resize frame", not (style & WS_CAPTION) and not (style & WS_THICKFRAME), f"style=0x{style & 0xFFFFFFFF:08X}")

# ---- 2. rendering: opaque pixels match the sprite; transparent pixels reveal the desktop
expected = qimage_to_pil(window._current_pixmap().toImage())
pw, ph = expected.size
alpha = expected.getchannel("A")
left, top, right, bottom = rect_of(hwnd)
shot_with = grab()
window.hide(); pump(0.5)
shot_without = grab()
window.show(); pump(0.5)
box = (left, top, left + pw, top + ph)
d_opaque = masked_mean_diff(shot_with.crop(box), expected, 255, alpha)
d_transp = masked_mean_diff(shot_with.crop(box), shot_without.crop(box), 0, alpha)
check("character is drawn pixel-accurately on screen", d_opaque < 3, f"mean colour error on opaque pixels = {d_opaque:.2f}/255")
check("transparent pixels show the desktop (no box/black/white background)", d_transp < 3, f"mean change on transparent pixels = {d_transp:.2f}/255")
margin = 40
shot_with.crop((max(0, left - margin), max(0, top - margin), min(SCREEN_W, right + margin), min(SCREEN_H, bottom + margin))).save(OUT / "1_character_on_desktop.png")

# ---- 3. hit-testing: transparent pixel vs opaque pixel (no clicks yet)
tdx, tdy = 8, 8
odx, ody = pw // 2, ph // 2
check("test point (8,8) is transparent in the sprite", alpha.getpixel((tdx, tdy)) == 0)
check("test point (centre) is opaque in the sprite", alpha.getpixel((odx, ody)) == 255)
check("Windows hit-test: transparent pixel is NOT the character window (click-through)", window_at(left + tdx, top + tdy) != hwnd)
check("Windows hit-test: opaque pixel IS the character window", window_at(left + odx, top + ody) == hwnd)

# ---- 4. an ordinary window created AFTER the character must not cover it
helper = Helper()
helper.setGeometry(window.geometry().adjusted(-150, -100, 0, 0))
helper.show(); helper.raise_(); helper.activateWindow(); pump(0.8)
shot_over = grab()
d_over = masked_mean_diff(shot_over.crop(box), expected, 255, alpha)
green = shot_over.getpixel((left + tdx, top + tdy))
check("ordinary window sits under the character (helper visible through transparent area)", green[1] > 200 and green[0] < 60 and green[2] < 60, f"pixel={green}")
check("character stays ON TOP of a window opened after it (always-on-top)", d_over < 3, f"mean colour error = {d_over:.2f}/255")
shot_over.crop((max(0, left - 200), max(0, top - 120), min(SCREEN_W, right + 40), min(SCREEN_H, bottom + 40))).save(OUT / "2_over_ordinary_window.png")

# ---- 5. REAL click on a transparent pixel must reach the application underneath
safe_target(left + tdx, top + tdy, "transparent area")
pos_before = window.pos()
move_cursor(left + tdx, top + tdy); press(LEFTDOWN); press(LEFTUP)
check("real click on a transparent pixel reaches the app below", len(helper.presses) == 1, f"helper received {len(helper.presses)} click(s)")
check("...and the character did not react to it", window.pos() == pos_before)

# ---- 6. REAL drag
fg_before = user32.GetForegroundWindow()
safe_target(left + odx, top + ody, "character body")
pos_before = window.pos()
clicks_before = len(helper.presses)
move_cursor(left + odx, top + ody); press(LEFTDOWN)
for i in range(1, 9):
    move_cursor(left + odx - 25 * i, top + ody - 15 * i)
press(LEFTUP)
moved = window.pos() - pos_before
want_x, want_y = -200 / dpr, -120 / dpr
check("real mouse drag moves the character by the mouse movement", abs(moved.x() - want_x) <= 2 and abs(moved.y() - want_y) <= 2,
      f"moved {moved.x()},{moved.y()} logical px, expected about {want_x:.0f},{want_y:.0f}")
check("dragging does not steal keyboard focus from the active window", user32.GetForegroundWindow() == fg_before)
check("the drag clicks did not leak to the window below", len(helper.presses) == clicks_before)
l2, t2, r2, b2 = rect_of(hwnd)
grab().crop((max(0, l2 - 100), max(0, t2 - 60), min(SCREEN_W, r2 + 100), min(SCREEN_H, b2 + 100))).save(OUT / "3_after_drag.png")

# ---- 7. REAL mouse wheel resizes, feet stay in place
left, top, right, bottom = rect_of(hwnd)
pw, ph = qimage_to_pil(window._current_pixmap().toImage()).size
g0 = window.geometry()
feet_x, feet_y = g0.x() + g0.width() / 2, g0.y() + g0.height()
safe_target(left + pw // 2, top + ph // 2, "wheel target")
move_cursor(left + pw // 2, top + ph // 2)
wheel_notch(+1)
g1 = window.geometry()
check("real mouse wheel enlarges the character", window.scale == round(CharacterWindow.DEFAULT_SCALE * CharacterWindow.WHEEL_ZOOM_PER_NOTCH, 2) and g1.width() > g0.width(),
      f"scale={window.scale}, {g0.width()}x{g0.height()} -> {g1.width()}x{g1.height()}")
check("...while its feet stay where they were", abs((g1.x() + g1.width() / 2) - feet_x) <= 1 and (g1.y() + g1.height()) == feet_y)
window.set_scale(CharacterWindow.DEFAULT_SCALE); pump(0.3)

# ---- 8. REAL right-click opens the menu, REAL click on "Exit" asks the app to exit
left, top, right, bottom = rect_of(hwnd)
pw, ph = qimage_to_pil(window._current_pixmap().toImage()).size
px, py = left + pw // 2, top + ph // 2
safe_target(px, py, "right-click target")
state = {}


def inspect_menu_then_click_exit():
    popup = QApplication.activePopupWidget()
    state["found"] = popup is not None and popup.isVisible()
    if popup is None:
        return
    time.sleep(0.5)  # let the menu finish its open animation
    state["labels"] = [a.text() for a in popup.actions() if not a.isSeparator()]
    pl, pt, pr, pb = rect_of(int(popup.winId()))
    grab().crop((max(0, min(pl, left) - 40), max(0, min(pt, top) - 40), min(SCREEN_W, max(pr, right) + 40), min(SCREEN_H, max(pb, bottom) + 40))).save(OUT / "4_context_menu.png")
    exit_action = next(a for a in popup.actions() if a.text() == "Exit")
    centre = popup.mapToGlobal(popup.actionGeometry(exit_action).center())
    ex_, ey_ = round(centre.x() * dpr), round(centre.y() * dpr)
    target = safe_target(ex_, ey_, "menu Exit item")
    state["exit_item_is_menu"] = target == int(popup.winId())
    move_cursor(ex_, ey_, wait=False); press(LEFTDOWN, wait=False); press(LEFTUP, wait=False)


QTimer.singleShot(900, inspect_menu_then_click_exit)
move_cursor(px, py); press(RIGHTDOWN); press(RIGHTUP)  # blocks here while the menu is open
pump(0.5)
check("real right-click opens the context menu", state.get("found"), f"entries={state.get('labels')}")
check("the menu is on top, hit-testable at its Exit item", state.get("exit_item_is_menu"))
check("real click on 'Exit' makes the character ask the app to exit", exits == [1])

# ---- cleanup
user32.SetCursorPos(cursor0.x, cursor0.y)
helper.close(); window.close(); pump(0.2)
passed = sum(ok for _, ok in results)
print(f"\n{passed}/{len(results)} checks passed   (screenshots: {OUT})")
sys.exit(0 if passed == len(results) else 1)
