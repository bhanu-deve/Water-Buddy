r"""End-to-end check: start the REAL `main.py` as its own process and drive it with the real mouse.

Run:   .\.venv\Scripts\python.exe tools\launch_check.py

WARNING: takes ~10 seconds and MOVES YOUR MOUSE / clicks. Don't touch the mouse while it runs.

Proves: the app starts, shows ONE window in the bottom-right corner (standing on the taskbar); a SCHEDULED REMINDER
arrives in the real program (it is started with --reminder-interval 0.15 --reminder-hours 00:00-00:00, so one comes
after 9 seconds at any time of day) and the character hops; a real right-click opens the right-click menu and Esc
closes it; a real LEFT click opens the small menu without nudging the character; and a real click on "Exit" in it ends
the whole program with exit code 0 - even while the character is still speaking the reminder through the real Windows
speech engine (started with --volume 0, so it genuinely speaks but makes no sound).
It is started from a different working directory on purpose: the app must not depend on it.

SAFETY: input is only injected after verifying the window under the target belongs to the launched app.
Screenshot: %TEMP%\water_assistant_checks\5_main_py_launch.png

Note: on Windows a venv's python.exe is only a launcher that starts the real interpreter as a CHILD
process, so the app's windows belong to a child PID. That is why this script follows the process tree.
"""
import ctypes
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
OUT = Path(tempfile.gettempdir()) / "water_assistant_checks"
OUT.mkdir(exist_ok=True)
TITLE = "Water Assistant Character"
BUBBLE_TITLE = "Water Assistant Speech Bubble"  # shown for a few seconds at start-up (the greeting)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor DPI aware (real pixel coordinates)

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetDpiForWindow.argtypes = [wintypes.HWND]
user32.GetDpiForWindow.restype = wintypes.UINT
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, wintypes.DWORD, ctypes.c_size_t]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260)]


kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

SCREEN_W, SCREEN_H = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"   ({detail})" if detail else ""), flush=True)


def process_tree(root_pid):
    """The launched process plus all its descendants."""
    parents = {}
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
    while more:
        parents[entry.th32ProcessID] = entry.th32ParentProcessID
        more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    kernel32.CloseHandle(snapshot)
    tree, grew = {root_pid}, True
    while grew:
        grew = False
        for pid, parent in parents.items():
            if parent in tree and pid not in tree:
                tree.add(pid)
                grew = True
    return tree


def visible_windows_of(pid):
    """Visible top-level windows owned by `pid` or any of its child processes."""
    found, tree = [], process_tree(pid)

    def callback(hwnd, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value in tree and user32.IsWindowVisible(hwnd):
            buffer = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buffer, 256)
            found.append((hwnd, buffer.value))
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return found


def rect_of(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def pid_of_window(hwnd):
    owner = wintypes.DWORD()
    if hwnd:
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
    return owner.value


def pid_at(x, y):
    return pid_of_window(user32.WindowFromPoint(wintypes.POINT(int(x), int(y))))


def move_cursor(x, y):
    user32.mouse_event(0x8001, round(x * 65535 / (SCREEN_W - 1)), round(y * 65535 / (SCREEN_H - 1)), 0, 0)
    time.sleep(0.15)


def press(flag):
    user32.mouse_event(flag, 0, 0, 0, 0)
    time.sleep(0.1)


def wait_for(predicate, timeout):
    end = time.time() + timeout
    while time.time() < end:
        value = predicate()
        if value:
            return value
        time.sleep(0.1)
    return None


cursor0 = wintypes.POINT()
user32.GetCursorPos(ctypes.byref(cursor0))
started = time.time()
# cwd is deliberately NOT the project folder: the app must not depend on the working directory.
proc = subprocess.Popen(
    [sys.executable, str(PROJECT / "main.py"), "--volume", "0", "--reminder-interval", "0.15", "--reminder-hours", "00:00-00:00"],
    cwd=str(OUT),
)
try:
    found = wait_for(lambda: next(((h, t) for h, t in visible_windows_of(proc.pid) if t == TITLE), None), 20)
    check("main.py starts and shows the character window", found is not None, f"after {time.time() - started:.1f}s")
    if not found:
        raise SystemExit(1)
    hwnd = found[0]
    time.sleep(0.5)

    left, top, right, bottom = rect_of(hwnd)
    dpr = user32.GetDpiForWindow(hwnd) / 96
    work = wintypes.RECT()
    user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work), 0)  # SPI_GETWORKAREA = screen minus the taskbar
    want_right, want_bottom = work.right - round(8 * dpr), work.bottom
    check("character starts in the bottom-right corner, standing on the taskbar",
          abs(right - want_right) <= 2 and abs(bottom - want_bottom) <= 2,
          f"rect=({left},{top},{right},{bottom}) work-area=({work.left},{work.top},{work.right},{work.bottom}) dpr={dpr}")
    shown = [title for _hwnd, title in visible_windows_of(proc.pid)]
    check("only the character (and its greeting bubble) are shown - no stray windows",
          shown.count(TITLE) == 1 and set(shown) <= {TITLE, BUBBLE_TITLE}, str(shown))
    check("the character greets with a speech bubble at start-up", BUBBLE_TITLE in shown)

    from PIL import ImageGrab

    shot = ImageGrab.grab(include_layered_windows=True).convert("RGB")
    shot.crop((max(0, left - 60), max(0, top - 60), min(SCREEN_W, right + 30), min(SCREEN_H, bottom + 30))).save(OUT / "5_main_py_launch.png")

    # ---- a scheduled reminder arrives in the real program (the character never hops by itself otherwise)
    hop_threshold = round(8 * dpr)
    hop_at = None
    give_up = time.time() + 30
    while hop_at is None and time.time() < give_up:
        if rect_of(hwnd)[1] <= top - hop_threshold:
            hop_at = time.time() - started
        else:
            time.sleep(0.02)
    check("a scheduled reminder arrives in the real program: the character hops ~9 s after the start",
          hop_at is not None and 7.5 <= hop_at <= 12.5, f"first hop {hop_at:.1f} s after the start" if hop_at else "no hop seen")
    check("...followed by its speech bubble", wait_for(lambda: any(t == BUBBLE_TITLE for _h, t in visible_windows_of(proc.pid)), 8) is not None)
    time.sleep(0.6)

    def popup_window():
        return next((h for h, t in visible_windows_of(proc.pid) if t not in (TITLE, BUBBLE_TITLE)), None)

    def aim_at_the_character():
        l, t, r, b = rect_of(hwnd)
        x, y = (l + r) // 2, (t + b) // 2
        if pid_at(x, y) not in process_tree(proc.pid):
            raise SystemExit("ABORT: the window under the character's body is not the launched app")
        return x, y

    def click_character(down, up):
        """Move the mouse onto the character and click - re-checking AFTER the move and right before the button goes
        down that the window under the mouse is still the program's own (a notification or another window could
        have appeared there in the meantime, and must never receive the click)."""
        x, y = aim_at_the_character()
        move_cursor(x, y)
        if pid_at(x, y) not in process_tree(proc.pid):
            raise SystemExit("ABORT: another window came in front of the character just before the click - not clicking")
        press(down); press(up)

    # ---- real right-click: the right-click menu opens, and Esc closes it again
    bx, by = aim_at_the_character()
    click_character(0x8, 0x10)  # right button down / up
    popup = wait_for(popup_window, 5)
    check("real right-click opens the right-click menu in the launched app", popup is not None)
    if popup is None:  # say what IS there, so a failure can be understood
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        print(f"   clicked at ({bx},{by}); character rect now {rect_of(hwnd)}; windows of the program:")
        for window_handle, window_title in visible_windows_of(proc.pid):
            window_class = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(window_handle, window_class, 256)
            print(f"     {window_class.value!r:48} title={window_title!r:34} rect={rect_of(window_handle)}")
    if popup:
        time.sleep(0.5)
        in_front = pid_of_window(user32.GetForegroundWindow()) in process_tree(proc.pid)
        check("the open menu is the window in front, so Esc goes to it and never to another program", in_front)
        if in_front:
            user32.keybd_event(0x1B, 0, 0, 0); time.sleep(0.05); user32.keybd_event(0x1B, 0, 2, 0)  # Esc down / up
            check("Esc closes the menu again", wait_for(lambda: popup_window() is None, 3) is not None)
    time.sleep(0.3)

    # ---- real LEFT click: the small menu opens (and the character does not move); then a click on its last row, "Exit"
    before_click = rect_of(hwnd)
    click_character(0x2, 0x4)  # left button down / up = a click
    popup = wait_for(popup_window, 5)
    check("real left click opens the small menu in the launched app", popup is not None)
    check("...and the click does not nudge the character", rect_of(hwnd) == before_click)
    if popup:
        time.sleep(0.8)  # let the menu finish its open animation, then measure its final position
        pl, pt, pr, pb = rect_of(popup)
        ex, ey = (pl + pr) // 2, pb - round(16 * dpr)  # middle of the last row = "Exit"
        if pid_at(ex, ey) not in process_tree(proc.pid):
            raise SystemExit(f"ABORT: the menu click point {(ex, ey)} is not over the launched app (menu rect {(pl, pt, pr, pb)})")
        speaking = any(t == BUBBLE_TITLE for _hwnd, t in visible_windows_of(proc.pid))
        check("Exit is clicked while the character is still speaking its reminder (the bubble is still up)", speaking,
              f"{time.time() - started:.1f} s after the start")
        move_cursor(ex, ey)
        clicked = time.time()
        press(0x2); press(0x4)  # left button down / up
        try:
            code = proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            code = None
        took = time.time() - clicked
        check("clicking 'Exit' closes the program completely with exit code 0, promptly", code == 0 and took < 5,
              f"exit code={code}, {took:.1f} s after the click")
finally:
    if proc.poll() is None:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)  # only OUR process tree
        print("(cleanup: the launched app was still running and was stopped)")
    user32.SetCursorPos(cursor0.x, cursor0.y)

print(f"\n{sum(results)}/{len(results)} checks passed   (screenshot: {OUT})")
sys.exit(0 if all(results) else 1)
