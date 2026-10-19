"""Глобальные хоткеи (RegisterHotKey) и события окон (SetWinEventHook) в мире Qt.

Оба механизма доставляют сообщения в поток, где их зарегистрировали, поэтому всё
создаётся в главном потоке Qt, а события выходят наружу обычными сигналами.
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, Signal
from PySide6.QtWidgets import QWidget

from lexoplay.hotkeys import Hotkey

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]

WM_HOTKEY = 0x0312

WINEVENTPROC = ctypes.WINFUNCTYPE(
    None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND, wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD
)
user32.SetWinEventHook.argtypes = [wintypes.UINT, wintypes.UINT, wintypes.HMODULE, WINEVENTPROC, wintypes.DWORD, wintypes.DWORD, wintypes.UINT]
user32.SetWinEventHook.restype = wintypes.HANDLE
user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]

EVENT_SYSTEM_FOREGROUND = 0x0003
EVENT_SYSTEM_MINIMIZESTART = 0x0016
EVENT_SYSTEM_MINIMIZEEND = 0x0017
EVENT_OBJECT_DESTROY = 0x8001
EVENT_OBJECT_LOCATIONCHANGE = 0x800B
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002
OBJID_WINDOW = 0


class _HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, on_hotkey) -> None:  # type: ignore[no-untyped-def]
        super().__init__()
        self._on_hotkey = on_hotkey

    def nativeEventFilter(self, eventType, message):  # type: ignore[no-untyped-def]  # noqa: N802
        et = bytes(eventType.data()) if hasattr(eventType, "data") else bytes(eventType)
        if et == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                self._on_hotkey(int(msg.wParam))
                return True, 0
        return False, 0


class HotkeyManager(QObject):
    """Две группы: global (работает всегда) и game (только пока игра в фокусе)."""

    triggered = Signal(str)
    failed = Signal(str, str)  # имя, текст хоткея

    def __init__(self) -> None:
        super().__init__()
        self._window = QWidget()  # невидимое окно-получатель WM_HOTKEY
        self._hwnd = int(self._window.winId())
        self._filter = _HotkeyFilter(self._dispatch)
        QCoreApplication.instance().installNativeEventFilter(self._filter)
        self._defs: dict[str, tuple[Hotkey, str]] = {}  # name -> (hotkey, group)
        self._ids: dict[int, str] = {}
        self._active: set[str] = set()
        self._next_id = 1

    def define(self, name: str, hotkey: Hotkey, group: str) -> None:
        self._defs[name] = (hotkey, group)

    def _register(self, name: str) -> None:
        if name in self._active:
            return
        hk, _group = self._defs[name]
        hid = self._next_id
        self._next_id += 1
        if user32.RegisterHotKey(self._hwnd, hid, hk.mods, hk.vk):
            self._ids[hid] = name
            self._active.add(name)
        else:
            err = ctypes.get_last_error()
            log.warning("хоткей %s (%s) не зарегистрирован, ошибка %s", name, hk.text, err)
            self.failed.emit(name, hk.text)

    def _unregister(self, name: str) -> None:
        for hid, n in list(self._ids.items()):
            if n == name:
                user32.UnregisterHotKey(self._hwnd, hid)
                del self._ids[hid]
        self._active.discard(name)

    def set_group(self, group: str, enabled: bool) -> None:
        for name, (_hk, g) in self._defs.items():
            if g != group:
                continue
            if enabled:
                self._register(name)
            else:
                self._unregister(name)

    def clear(self) -> None:
        for name in list(self._active):
            self._unregister(name)
        self._defs.clear()

    def _dispatch(self, hid: int) -> None:
        name = self._ids.get(hid)
        if name:
            self.triggered.emit(name)


class WindowEvents(QObject):
    """Смена активного окна (глобально) и движение/закрытие окна игры (только её процесс)."""

    foreground = Signal(int)
    game_moved = Signal(int)
    game_minimized = Signal(int, bool)
    game_destroyed = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self._fg_proc = WINEVENTPROC(self._on_foreground)
        self._fg_hook = user32.SetWinEventHook(
            EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND, None, self._fg_proc, 0, 0, WINEVENT_OUTOFCONTEXT
        )
        self._game_proc = WINEVENTPROC(self._on_game_event)
        self._game_hooks: list[int] = []
        self._game_hwnd = 0

    def _on_foreground(self, _hook, _event, hwnd, id_object, _id_child, _thread, _time):  # type: ignore[no-untyped-def]
        if hwnd and id_object == OBJID_WINDOW:
            self.foreground.emit(int(hwnd))

    def _on_game_event(self, _hook, event, hwnd, id_object, _id_child, _thread, _time):  # type: ignore[no-untyped-def]
        if not hwnd or id_object != OBJID_WINDOW or int(hwnd) != self._game_hwnd:
            return
        if event == EVENT_OBJECT_LOCATIONCHANGE:
            self.game_moved.emit(int(hwnd))
        elif event == EVENT_SYSTEM_MINIMIZESTART:
            self.game_minimized.emit(int(hwnd), True)
        elif event == EVENT_SYSTEM_MINIMIZEEND:
            self.game_minimized.emit(int(hwnd), False)
        elif event == EVENT_OBJECT_DESTROY:
            self.game_destroyed.emit(int(hwnd))

    def watch_game(self, hwnd: int, pid: int) -> None:
        if hwnd == self._game_hwnd:
            return
        self.unwatch_game()
        self._game_hwnd = hwnd
        for lo, hi in (
            (EVENT_SYSTEM_MINIMIZESTART, EVENT_SYSTEM_MINIMIZEEND),
            (EVENT_OBJECT_DESTROY, EVENT_OBJECT_DESTROY),
            (EVENT_OBJECT_LOCATIONCHANGE, EVENT_OBJECT_LOCATIONCHANGE),
        ):
            h = user32.SetWinEventHook(lo, hi, None, self._game_proc, pid, 0, WINEVENT_OUTOFCONTEXT)
            if h:
                self._game_hooks.append(h)

    def unwatch_game(self) -> None:
        for h in self._game_hooks:
            user32.UnhookWinEvent(h)
        self._game_hooks.clear()
        self._game_hwnd = 0

    def close(self) -> None:
        self.unwatch_game()
        if self._fg_hook:
            user32.UnhookWinEvent(self._fg_hook)
            self._fg_hook = None
