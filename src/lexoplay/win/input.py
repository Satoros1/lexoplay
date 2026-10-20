"""Нажал ли игрок что-нибудь: клик, колесо, клавиша. Через Raw Input, без хуков в игру.

Перевод должен пропадать после действия игрока (пропустил реплику), а не потому, что
в окне диалога что-то мигнуло. Raw Input с флагом INPUTSINK присылает нашему скрытому
окну события мыши и клавиатуры, даже когда в фокусе игра. Это не перехват: игра
получает ввод как обычно, а мы только узнаём, что он был. Включается только пока
на экране перевод. Движение мыши не считается: только кнопки, колесо и клавиши.
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, Signal
from PySide6.QtWidgets import QWidget

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)

WM_INPUT = 0x00FF
RID_INPUT = 0x10000003
RIM_TYPEMOUSE = 0
RIM_TYPEKEYBOARD = 1
RIDEV_REMOVE = 0x00000001
RIDEV_INPUTSINK = 0x00000100
RI_KEY_BREAK = 0x01
MOUSE_MOVE_ABSOLUTE = 0x01  # планшет, удалённый рабочий стол: координаты, а не сдвиг
# кнопки вниз (левая, правая, средняя, 4, 5), колесо и отпускание левой: многие новеллы
# листают реплику по отпусканию кнопки, и проверка сразу после него ловит смену быстрее
MOUSE_ACTION = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0040 | 0x0100 | 0x0400 | 0x0800


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
        ("dwFlags", wintypes.DWORD),
        ("hwndTarget", wintypes.HWND),
    ]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType", wintypes.DWORD),
        ("dwSize", wintypes.DWORD),
        ("hDevice", wintypes.HANDLE),
        ("wParam", wintypes.WPARAM),
    ]


class _MouseButtons(ctypes.Structure):
    _fields_ = [("usButtonFlags", wintypes.USHORT), ("usButtonData", wintypes.USHORT)]


class _MouseUnion(ctypes.Union):
    _fields_ = [("ulButtons", wintypes.ULONG), ("buttons", _MouseButtons)]


class RAWMOUSE(ctypes.Structure):
    _fields_ = [
        ("usFlags", wintypes.USHORT),
        ("u", _MouseUnion),
        ("ulRawButtons", wintypes.ULONG),
        ("lLastX", wintypes.LONG),
        ("lLastY", wintypes.LONG),
        ("ulExtraInformation", wintypes.ULONG),
    ]


class RAWKEYBOARD(ctypes.Structure):
    _fields_ = [
        ("MakeCode", wintypes.USHORT),
        ("Flags", wintypes.USHORT),
        ("Reserved", wintypes.USHORT),
        ("VKey", wintypes.USHORT),
        ("Message", wintypes.UINT),
        ("ExtraInformation", wintypes.ULONG),
    ]


class _RawData(ctypes.Union):
    _fields_ = [("mouse", RAWMOUSE), ("keyboard", RAWKEYBOARD)]


class RAWINPUT(ctypes.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("data", _RawData)]


user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT]
user32.RegisterRawInputDevices.restype = wintypes.BOOL
user32.GetRawInputData.argtypes = [
    wintypes.HANDLE,
    wintypes.UINT,
    ctypes.c_void_p,
    ctypes.POINTER(wintypes.UINT),
    wintypes.UINT,
]
user32.GetRawInputData.restype = wintypes.UINT


def is_action(raw: RAWINPUT, ignore_vks: set[int]) -> bool:
    """Клик, колесо или нажатие клавиши (не отпускание, не наши горячие клавиши)."""
    if raw.header.dwType == RIM_TYPEMOUSE:
        return bool(raw.data.mouse.u.buttons.usButtonFlags & MOUSE_ACTION)
    if raw.header.dwType == RIM_TYPEKEYBOARD:
        kb = raw.data.keyboard
        return not (kb.Flags & RI_KEY_BREAK) and kb.VKey not in ignore_vks
    return False


class _Filter(QAbstractNativeEventFilter):
    def __init__(self, hwnd: int, on_input) -> None:  # type: ignore[no-untyped-def]
        super().__init__()
        self._hwnd = hwnd
        self._on_input = on_input

    def nativeEventFilter(self, eventType, message):  # type: ignore[no-untyped-def]  # noqa: N802
        et = bytes(eventType.data()) if hasattr(eventType, "data") else bytes(eventType)
        if et == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_INPUT and int(msg.hWnd or 0) == self._hwnd:
                self._on_input(msg.lParam)
        return False, 0


def action_kind(raw: RAWINPUT) -> str:
    return "mouse" if raw.header.dwType == RIM_TYPEMOUSE else "key"


class InputWatch(QObject):
    """pressed(kind): игрок кликнул или крутанул колесо ("mouse") или нажал клавишу ("key") где угодно."""

    pressed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._window = QWidget()  # невидимое окно-получатель WM_INPUT
        self._hwnd = int(self._window.winId())
        self._filter = _Filter(self._hwnd, self._on_input)
        QCoreApplication.instance().installNativeEventFilter(self._filter)
        self.active = False
        self.ignore_vks: set[int] = set()
        self.travel = 0  # сколько мышь проехала (сырые отсчёты): видно, что курсор держит игра
        self._buf = RAWINPUT()

    def _devices(self, flags: int, hwnd: int | None) -> bool:
        devs = (RAWINPUTDEVICE * 2)(
            RAWINPUTDEVICE(0x01, 0x02, flags, hwnd),  # мышь
            RAWINPUTDEVICE(0x01, 0x06, flags, hwnd),  # клавиатура
        )
        return bool(user32.RegisterRawInputDevices(devs, 2, ctypes.sizeof(RAWINPUTDEVICE)))

    def start(self) -> bool:
        if self.active:
            return True
        self.active = self._devices(RIDEV_INPUTSINK, self._hwnd)
        if not self.active:
            log.warning("Raw Input не включился, ошибка %s", ctypes.get_last_error())
        return self.active

    def stop(self) -> None:
        if self.active:
            self._devices(RIDEV_REMOVE, None)
            self.active = False

    def _on_input(self, lparam: int) -> None:
        size = wintypes.UINT(ctypes.sizeof(RAWINPUT))
        got = user32.GetRawInputData(
            lparam, RID_INPUT, ctypes.byref(self._buf), ctypes.byref(size), ctypes.sizeof(RAWINPUTHEADER)
        )
        if got in (0, 0xFFFFFFFF):
            return
        if self._buf.header.dwType == RIM_TYPEMOUSE and not self._buf.data.mouse.usFlags & MOUSE_MOVE_ABSOLUTE:
            self.travel += abs(self._buf.data.mouse.lLastX) + abs(self._buf.data.mouse.lLastY)
        if is_action(self._buf, self.ignore_vks):
            self.pressed.emit(action_kind(self._buf))

    def close(self) -> None:
        self.stop()
        QCoreApplication.instance().removeNativeEventFilter(self._filter)
