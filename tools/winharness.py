r"""Shared plumbing for the real-desktop check tools: Win32 window and mouse helpers, screenshots, PASS/FAIL report.

(desktop_check.py, launch_check.py and animation_check.py each carry their own older copy of this; new tools
use this module.)

SAFETY: input is only ever injected after safe_target() has verified that the window under the point belongs to
THIS process (the character, the bubble, a helper window or one of the program's menus). Otherwise the tool aborts
without clicking, so a window of another program that happens to be in the way is never touched.
"""
import atexit
import ctypes
import os
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

from PIL import ImageGrab
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

OUT = Path(tempfile.gettempdir()) / "water_assistant_checks"
OUT.mkdir(exist_ok=True)

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]

LEFTDOWN, LEFTUP, RIGHTDOWN, RIGHTUP, MOVE_ABS = 0x2, 0x4, 0x8, 0x10, 0x8001

results: list[bool] = []
_restorers: list = []


def check(name: str, ok, detail: str = "") -> bool:
    results.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"   ({detail})" if detail else ""), flush=True)
    return bool(ok)


def finish() -> int:
    """Print the summary and return the process exit code."""
    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed   (screenshots: {OUT})")
    return 0 if passed == len(results) else 1


def on_exit(function) -> None:
    """Run `function` when the tool ends - even if it stops early (an ABORT, an error): windows get closed etc."""
    _restorers.append(function)


def start_session() -> None:
    """Remember where the mouse is and put it back when the tool ends."""
    cursor = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(cursor))
    on_exit(lambda: user32.SetCursorPos(cursor.x, cursor.y))
    atexit.register(lambda: [function() for function in reversed(_restorers)])


# ------------------------------------------------------------------ time and events
def pump(seconds: float) -> None:
    app = QApplication.instance()
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def run_for(seconds: float) -> None:
    """Run Qt's real event loop for a while (it sleeps properly)."""
    loop = QEventLoop()
    QTimer.singleShot(int(seconds * 1000), loop.quit)
    loop.exec()


def wait_until(condition, timeout: float = 10.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if condition():
            return True
        pump(0.01)
    return bool(condition())


# ------------------------------------------------------------------ windows
def screen_size() -> tuple[int, int]:
    """The screen in REAL pixels (Qt makes the process DPI-aware once the QApplication exists)."""
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def pid_of(hwnd) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def rect_of(hwnd) -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def window_at(x, y):
    return user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))


def foreground():
    return user32.GetForegroundWindow()


def describe_window(hwnd) -> str:
    """Which application a window belongs to. Class name only: window titles can contain private text."""
    if not hwnd:
        return "no window"
    cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls, 256)
    owner = "this tool" if pid_of(hwnd) == os.getpid() else f"another app (pid {pid_of(hwnd)})"
    return f"{owner}, class {cls.value!r}"


def safe_target(x, y, label):
    hwnd = window_at(x, y)
    if not hwnd or pid_of(hwnd) != os.getpid():
        raise SystemExit(
            f"ABORT: '{label}' point ({x},{y}) is not over a window of this tool - refusing to inject input "
            f"(there is: {describe_window(hwnd)})"
        )
    return hwnd


# ------------------------------------------------------------------ the real mouse
def move_cursor(x, y, wait=True) -> None:
    w, h = screen_size()
    user32.mouse_event(MOVE_ABS, round(x * 65535 / (w - 1)), round(y * 65535 / (h - 1)), 0, 0)
    pump(0.04) if wait else time.sleep(0.05)


def press(flag, wait=True) -> None:
    user32.mouse_event(flag, 0, 0, 0, 0)
    pump(0.04) if wait else time.sleep(0.05)


def center_of(menu, text, dpr):
    """Screen position (real pixels) of the middle of the entry `text` in an open menu."""
    action = next(a for a in menu.actions() if a.text() == text)
    c = menu.mapToGlobal(menu.actionGeometry(action).center())
    return round(c.x() * dpr), round(c.y() * dpr)


# ------------------------------------------------------------------ pictures
def grab():
    return ImageGrab.grab(include_layered_windows=True).convert("RGB")


def snap(name: str, *hwnds, margin: int = 30) -> None:
    """Save a screenshot cropped around the given windows."""
    w, h = screen_size()
    rects = [rect_of(hwnd) for hwnd in hwnds]
    box = (
        max(0, min(r[0] for r in rects) - margin),
        max(0, min(r[1] for r in rects) - margin),
        min(w, max(r[2] for r in rects) + margin),
        min(h, max(r[3] for r in rects) + margin),
    )
    grab().crop(box).save(OUT / f"{name}.png")
