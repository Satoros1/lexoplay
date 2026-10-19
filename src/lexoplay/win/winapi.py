"""Тонкие обёртки над user32/kernel32 через ctypes: окна, процессы, мониторы, Magpie.

Процесс работает в режиме Per-Monitor DPI V2 (Qt 6 включает его сам при создании
QApplication), поэтому все координаты здесь в физических пикселях.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

from lexoplay.geometry import Rect, fit_aspect

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

HWND = wintypes.HWND

# --- сигнатуры: на 64-битах без них HWND обрезается до int32 ---
user32.GetForegroundWindow.restype = HWND
user32.GetWindowThreadProcessId.argtypes = [HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetClientRect.argtypes = [HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.argtypes = [HWND, ctypes.POINTER(wintypes.RECT)]
user32.ClientToScreen.argtypes = [HWND, ctypes.POINTER(wintypes.POINT)]
user32.IsWindow.argtypes = [HWND]
user32.IsWindowVisible.argtypes = [HWND]
user32.IsIconic.argtypes = [HWND]
user32.GetWindowTextLengthW.argtypes = [HWND]
user32.GetWindowTextW.argtypes = [HWND, wintypes.LPWSTR, ctypes.c_int]
user32.MonitorFromWindow.argtypes = [HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype = wintypes.HMONITOR
user32.SetWindowPos.argtypes = [HWND, HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetForegroundWindow.argtypes = [HWND]
user32.BringWindowToTop.argtypes = [HWND]
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.GetAncestor.argtypes = [HWND, wintypes.UINT]
user32.GetAncestor.restype = HWND
user32.SetWindowDisplayAffinity.argtypes = [HWND, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
user32.ShowWindow.argtypes = [HWND, ctypes.c_int]
user32.GetWindow.argtypes = [HWND, wintypes.UINT]
user32.GetWindow.restype = HWND
user32.GetWindowLongPtrW.argtypes = [HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t

try:
    dwmapi = ctypes.WinDLL("dwmapi")
    dwmapi.DwmGetWindowAttribute.argtypes = [HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
except OSError:  # pragma: no cover
    dwmapi = None

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MONITOR_DEFAULTTONEAREST = 2
HWND_TOPMOST = HWND(-1)
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_NOOWNERZORDER = 0x0200
GA_ROOT = 2
WDA_EXCLUDEFROMCAPTURE = 0x00000011
SW_RESTORE = 9
GW_OWNER = 4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
DWMWA_CLOAKED = 14


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]


def _hwnd(h: int | None) -> int:
    return int(h or 0)


def foreground_window() -> int:
    return _hwnd(user32.GetForegroundWindow())


def root_window(hwnd: int) -> int:
    return _hwnd(user32.GetAncestor(hwnd, GA_ROOT)) or hwnd


def window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def window_thread(hwnd: int) -> int:
    return int(user32.GetWindowThreadProcessId(hwnd, None))


def process_path(pid: int) -> str | None:
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return None
        return buf.value
    finally:
        kernel32.CloseHandle(h)


def process_exe(pid: int) -> str | None:
    path = process_path(pid)
    return os.path.basename(path).lower() if path else None


def window_title(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def is_window(hwnd: int) -> bool:
    return bool(hwnd) and bool(user32.IsWindow(hwnd))


def is_minimized(hwnd: int) -> bool:
    return bool(user32.IsIconic(hwnd))


def is_visible(hwnd: int) -> bool:
    return bool(user32.IsWindowVisible(hwnd))


def _rect(r: wintypes.RECT) -> Rect:
    return Rect(r.left, r.top, r.right - r.left, r.bottom - r.top)


def window_rect(hwnd: int) -> Rect:
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return _rect(r)


def client_rect_screen(hwnd: int) -> Rect:
    """Клиентская область окна (без рамки и заголовка) в экранных координатах."""
    r = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return Rect(pt.x, pt.y, r.right - r.left, r.bottom - r.top)


def monitor_rect(hwnd: int) -> Rect:
    mon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(mon, ctypes.byref(info))
    return _rect(info.rcMonitor)


def top_level_windows() -> list[int]:
    out: list[int] = []

    @WNDENUMPROC
    def _cb(hwnd, _lparam):  # type: ignore[no-untyped-def]
        out.append(_hwnd(hwnd))
        return True

    user32.EnumWindows(_cb, 0)
    return out


def _same(a: Rect, b: Rect, tol: float = 3) -> bool:
    return abs(a.x - b.x) <= tol and abs(a.y - b.y) <= tol and abs(a.w - b.w) <= tol and abs(a.h - b.h) <= tol


def find_magpie_window(mon: Rect) -> int | None:
    """Полноэкранное окно Magpie на этом мониторе, если игра сейчас растянута им."""
    for hwnd in top_level_windows():
        if not is_visible(hwnd) or is_minimized(hwnd):
            continue
        if not _same(window_rect(hwnd), mon):
            continue
        if (process_exe(window_pid(hwnd)) or "") == "magpie.exe":
            return hwnd
    return None


def display_rect(hwnd: int, magpie_mode: str = "auto") -> tuple[Rect, bool]:
    """Где игра реально видна на экране. Возвращает (прямоугольник, растянута ли)."""
    client = client_rect_screen(hwnd)
    if magpie_mode == "never" or client.empty:
        return client, False
    mon = monitor_rect(hwnd)
    if magpie_mode == "always" or find_magpie_window(mon) is not None:
        return fit_aspect(client.w, client.h, mon), True
    return client, False


def place_topmost(hwnd: int, rect: Rect) -> None:
    """Поставить наше окно поверх всех в физических координатах, не забирая фокус."""
    x, y, w, h = rect.to_int()
    user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, w, h, SWP_NOACTIVATE | SWP_NOOWNERZORDER)


def exclude_from_capture(hwnd: int) -> bool:
    """Наши окна не попадут в собственный снимок экрана (Windows 10 2004+)."""
    return bool(user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE))


def force_foreground(hwnd: int) -> None:
    """Вернуть фокус окну. AttachThreadInput снимает запрет Windows на «кражу» фокуса."""
    if not is_window(hwnd):
        return
    fg = foreground_window()
    cur = kernel32.GetCurrentThreadId()
    fg_tid = window_thread(fg) if fg else 0
    attached = bool(fg_tid and fg_tid != cur and user32.AttachThreadInput(fg_tid, cur, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(fg_tid, cur, False)


def own_pid() -> int:
    return os.getpid()


def restore_window(hwnd: int) -> None:
    """Развернуть свёрнутую игру (полноэкранные игры сворачиваются, когда теряют фокус)."""
    if is_window(hwnd) and is_minimized(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)


def set_no_activate(hwnd: int) -> None:
    """Окно получает клики, но не забирает фокус: игра остаётся активной и не сворачивается."""
    style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE)


def _cloaked(hwnd: int) -> bool:
    """Скрытые системой окна (приложения из Store на других рабочих столах) выглядят видимыми."""
    if dwmapi is None:
        return False
    val = wintypes.DWORD()
    if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(val), ctypes.sizeof(val)) != 0:
        return False
    return bool(val.value)


def app_windows() -> list[tuple[int, str, str, str, Rect, bool]]:
    """Окна, которые видно на панели задач: (hwnd, заголовок, exe, путь, размер, свёрнуто)."""
    own = os.getpid()
    out: list[tuple[int, str, str, str, Rect, bool]] = []
    for hwnd in top_level_windows():
        if not is_visible(hwnd) or user32.GetWindow(hwnd, GW_OWNER):
            continue
        if user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW or _cloaked(hwnd):
            continue
        title = window_title(hwnd).strip()
        pid = window_pid(hwnd)
        if not title or pid == own:
            continue
        path = process_path(pid) or ""
        exe = os.path.basename(path).lower() if path else ""
        if not exe:
            continue
        out.append((hwnd, title, exe, path, client_rect_screen(hwnd), is_minimized(hwnd)))
    return out


def find_game_window(exe: str) -> int | None:
    """Самое большое окно этой игры, если она запущена."""
    best, best_area = None, -1.0
    for hwnd, _title, wexe, _path, rect, minimized in app_windows():
        if wexe == exe.lower():
            area = 0.0 if minimized else rect.w * rect.h
            if area > best_area:
                best, best_area = hwnd, area
    return best
