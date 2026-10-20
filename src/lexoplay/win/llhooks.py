"""Режим слов: клики над слоем и клавиши режима не доходят до игры (WH_MOUSE_LL, WH_KEYBOARD_LL).

Окно слоя слов и так ловит клики над игрой, но многие игры читают ввод мимо оконных
сообщений: Raw Input, DirectInput, опрос GetAsyncKeyState. Им всё равно, какое окно под
курсором: игра в фокусе, значит, клик её. Так клик по слову пропускал реплику, а Enter
(«сохранить слово») мог её пролистать.

Низкоуровневые хуки Windows вызывает раньше окон: забранное событие не доходит ни до окон,
ни до GetAsyncKeyState, ни до DirectInput, который сам живёт на таких хуках. Raw Input игры в
фокусе хук не останавливает (Windows раздаёт его раньше хуков), поэтому на время режима App
ещё и забирает фокус у игры (App._words_focus): без фокуса Raw Input игре не приходит.
Что забрали, хук сообщает в поток Qt сигналами, и режим слов отвечает сам: клик выбирает
слово. Что забирать, решает Shield (lexoplay/shield.py).

Хуки ставятся в своём потоке со своим циклом сообщений: Windows ждёт ответа хука на каждое
событие мыши в системе, и занятый главный поток тормозил бы курсор. И стоят только пока
открыт режим (плюс пока не отпустят забранную кнопку): держать их всё время незачем.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes

from PySide6.QtCore import QObject, QTimer, Signal

from lexoplay.hotkeys import MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN
from lexoplay.shield import KEY_DOWN, WM_MOUSEMOVE, Shield, needs_target

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
HC_ACTION = 0
WM_APP = 0x8000
WM_CHECK = WM_APP + 1  # режим закрыт: не пора ли снимать хуки
WM_STOP = WM_APP + 2  # снять хуки сейчас
PM_NOREMOVE = 0
GA_ROOT = 2
LINGER_MS = 10_000  # после закрытия режима ждём отпускания забранной кнопки не дольше этого

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
user32.PeekMessageW.restype = wintypes.BOOL
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetCurrentThreadId.restype = wintypes.DWORD


def _mods() -> int:
    """Зажатые Ctrl, Shift, Alt, Win в битах MOD_*, как в сочетаниях клавиш."""

    def down(vk: int) -> bool:
        return bool(user32.GetAsyncKeyState(vk) & 0x8000)

    mods = 0
    if down(0x11):
        mods |= MOD_CONTROL
    if down(0x10):
        mods |= MOD_SHIFT
    if down(0x12):
        mods |= MOD_ALT
    if down(0x5B) or down(0x5C):
        mods |= MOD_WIN
    return mods


def to_local(hwnd: int, x: int, y: int, dpr: float) -> tuple[float, float]:
    """Точка экрана из хука (физические пиксели) -> точка в окне в логических пикселях Qt."""
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    k = dpr or 1.0
    return (x - r.left) / k, (y - r.top) / k


class InputShield(QObject):
    """Хуки на время режима слов.

    clicked(vk, x, y, mods): левая (1) или правая (2) кнопка над слоем, точка экрана, модификаторы.
    released(vk): её отпустили (отпускание тоже забрали у игры).
    button(vk): средняя или боковая кнопка над слоем (на неё может быть назначено действие).
    action(name): клавиша режима слов (words_esc, words_left, ...), которую забрали у игры."""

    clicked = Signal(int, int, int, int)
    released = Signal(int)
    button = Signal(int)
    action = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.logic = Shield()
        self.blocked = 0  # сколько событий забрали (для самопроверки и отчёта)
        self.key_calls = 0  # сколько раз Windows звала хук клавиатуры
        self.last_key: tuple[int, int, int] | None = None  # (сообщение, vk, модификаторы)
        self.errors: list[str] = []
        self.failed = ""  # почему хуки не встали
        self._layer = 0
        self._lock = threading.Lock()
        self._running = False
        self._tid = 0
        self._thread: threading.Thread | None = None
        self._mouse_proc = HOOKPROC(self._on_mouse)  # ссылки держим, пока хуки стоят
        self._key_proc = HOOKPROC(self._on_key)
        self._linger = QTimer(self)
        self._linger.setSingleShot(True)
        self._linger.setInterval(LINGER_MS)
        self._linger.timeout.connect(self._force_stop)

    @property
    def running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------ из потока Qt
    def start(self, layer_hwnd: int, keys: dict[tuple[int, int], str], repeat: set[str] | frozenset[str]) -> bool:
        """Режим слов открыт поверх игры окном layer_hwnd: забирать клики над ним и клавиши keys."""
        self._linger.stop()
        with self._lock:
            self._layer = layer_hwnd
            self.logic.keys = dict(keys)
            self.logic.repeat = frozenset(repeat)
            self.logic.active = True
            if self._running:
                return True
            self._running = True
            ready = threading.Event()
            result = [False]
            self._thread = threading.Thread(target=self._run, args=(ready, result), name="lexoplay-shield", daemon=True)
            self._thread.start()
        ready.wait(1.0)
        return result[0]

    def stop(self) -> None:
        """Режим слов закрыт: новых нажатий не забираем. Хуки снимутся, когда отпустят забранное."""
        with self._lock:
            self.logic.active = False
            tid = self._tid if self._running else 0
        if tid:
            user32.PostThreadMessageW(tid, WM_CHECK, 0, 0)
            self._linger.start()

    def close(self) -> None:
        self._linger.stop()
        with self._lock:
            self.logic.active = False
            tid = self._tid if self._running else 0
            thread = self._thread
        if tid:
            user32.PostThreadMessageW(tid, WM_STOP, 0, 0)
        if thread is not None:
            thread.join(1.0)

    def _force_stop(self) -> None:
        with self._lock:
            tid = self._tid if self._running and not self.logic.active else 0
        if tid:
            user32.PostThreadMessageW(tid, WM_STOP, 0, 0)

    # ------------------------------------------------------------------ поток хуков
    def _run(self, ready: threading.Event, result: list[bool]) -> None:
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, WM_APP, WM_APP, PM_NOREMOVE)  # у потока появилась очередь
        self._tid = int(kernel32.GetCurrentThreadId())
        module = kernel32.GetModuleHandleW(None)
        hooks = []
        for kind, proc in ((WH_MOUSE_LL, self._mouse_proc), (WH_KEYBOARD_LL, self._key_proc)):
            h = user32.SetWindowsHookExW(kind, proc, module, 0)
            if h:
                hooks.append(h)
            else:
                self.failed = f"SetWindowsHookEx({kind}): ошибка {ctypes.get_last_error()}"
                log.warning("хук %s не встал: %s", kind, self.failed)
        result[0] = len(hooks) == 2
        if not hooks:
            with self._lock:
                self._running = False
            ready.set()
            return
        ready.set()
        try:
            while True:
                got = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if got in (0, -1):
                    break
                if msg.message == WM_STOP:
                    with self._lock:
                        if self.logic.active:
                            continue  # режим успели открыть снова
                        self.logic.held_buttons.clear()
                        self.logic.held_keys.clear()
                        self._running = False
                    break
                if msg.message == WM_CHECK:
                    with self._lock:
                        if self.logic.idle:
                            self._running = False
                            break
        finally:
            for h in hooks:
                user32.UnhookWindowsHookEx(h)

    def _over_layer(self, pt: wintypes.POINT) -> bool:
        """Клик пришёлся в слой слов (а не в окно поверх него, другой монитор, рамку игры)."""
        h = user32.WindowFromPoint(pt)
        if not h:
            return False
        root = user32.GetAncestor(h, GA_ROOT) or h
        return int(root) == self._layer

    def _done(self) -> None:
        if self.logic.idle:
            user32.PostThreadMessageW(self._tid, WM_CHECK, 0, 0)

    def _on_mouse(self, code: int, wparam: int, lparam: int) -> int:
        logic = self.logic
        if code == HC_ACTION and wparam != WM_MOUSEMOVE and (logic.active or logic.held_buttons):
            try:
                info = MSLLHOOKSTRUCT.from_address(lparam)
                over = logic.active and needs_target(wparam) and self._over_layer(info.pt)
                block, event = logic.mouse(wparam, int(info.mouseData), over)
                if event is not None:
                    kind, vk = event
                    if kind == "click":
                        self.clicked.emit(int(vk), int(info.pt.x), int(info.pt.y), _mods())
                    elif kind == "release":
                        self.released.emit(int(vk))
                    else:
                        self.button.emit(int(vk))
                if block:
                    self.blocked += 1
                    self._done()
                    return 1
            except Exception as exc:
                self.errors.append(repr(exc)[:200])
                log.exception("хук мыши")
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _on_key(self, code: int, wparam: int, lparam: int) -> int:
        logic = self.logic
        self.key_calls += 1
        if code == HC_ACTION and (logic.active or logic.held_keys):
            try:
                info = KBDLLHOOKSTRUCT.from_address(lparam)
                mods = _mods() if wparam in KEY_DOWN else 0
                self.last_key = (int(wparam), int(info.vkCode), mods)
                block, event = logic.key(wparam, int(info.vkCode), mods)
                if event is not None:
                    self.action.emit(str(event[1]))
                if block:
                    self.blocked += 1
                    self._done()
                    return 1
            except Exception as exc:
                self.errors.append(repr(exc)[:200])
                log.exception("хук клавиатуры")
        return user32.CallNextHookEx(None, code, wparam, lparam)
