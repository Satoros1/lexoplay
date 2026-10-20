"""Поддельная игра для самопроверки в CI: отдельный процесс, ввод читает так, как читают игры.

Raw Input без INPUTSINK (как у игр: приходит, только пока окно в фокусе), оконные сообщения
и опрос GetAsyncKeyState. Считает нажатия левой кнопки, колесо и клавиши, а счётчики пишет
в JSON-файл, как только они меняются. Закрывается по WM_CLOSE.

Запуск: LexoPlay.exe --fake-game state.json --fake-rect 120,120,800,500
"""

from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_TIMER = 0x0113
WM_INPUT = 0x00FF
WM_KEYDOWN = 0x0100
WM_LBUTTONDOWN = 0x0201
WM_MOUSEWHEEL = 0x020A
WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000
RID_INPUT = 0x10000003
RIM_TYPEMOUSE = 0
RIM_TYPEKEYBOARD = 1
RI_KEY_BREAK = 0x01
COLOR_WINDOW = 5
VK_LBUTTON = 0x01
VK_RETURN = 0x0D

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


def run(state: str, rect: str) -> int:
    from lexoplay.win.input import RAWINPUT, RAWINPUTDEVICE, RAWINPUTHEADER

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]  # fmt: skip
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
    user32.GetRawInputData.argtypes = [wintypes.HANDLE, wintypes.UINT, ctypes.c_void_p, ctypes.POINTER(wintypes.UINT), wintypes.UINT]
    user32.GetRawInputData.restype = wintypes.UINT
    user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT]
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # как Qt у LexoPlay: физические пиксели
    except (AttributeError, OSError):
        pass

    out = Path(state)
    counts: dict[str, object] = {
        "pid": os.getpid(), "hwnd": 0, "foreground": False,
        "raw_down": 0, "raw_up": 0, "raw_wheel": 0, "raw_keys": 0, "msg_down": 0, "msg_wheel": 0, "msg_keys": 0,
        "async_down": 0, "async_enter": 0,
    }  # fmt: skip
    last = {"lb": False, "enter": False, "saved": ""}
    buf = RAWINPUT()

    def save() -> None:
        text = json.dumps(counts)
        if text != last["saved"]:
            tmp = out.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, out)
            last["saved"] = text

    def bump(key: str) -> None:
        counts[key] = int(counts[key]) + 1  # type: ignore[call-overload]

    def proc(hwnd, msg, wparam, lparam):  # type: ignore[no-untyped-def]
        if msg == WM_INPUT:
            size = wintypes.UINT(ctypes.sizeof(RAWINPUT))
            got = user32.GetRawInputData(lparam, RID_INPUT, ctypes.byref(buf), ctypes.byref(size), ctypes.sizeof(RAWINPUTHEADER))
            if got not in (0, 0xFFFFFFFF):
                if buf.header.dwType == RIM_TYPEMOUSE:
                    flags = buf.data.mouse.u.buttons.usButtonFlags
                    if flags & 0x0001:
                        bump("raw_down")
                    if flags & 0x0002:
                        bump("raw_up")
                    if flags & 0x0400:
                        bump("raw_wheel")
                elif buf.header.dwType == RIM_TYPEKEYBOARD and not buf.data.keyboard.Flags & RI_KEY_BREAK:
                    bump("raw_keys")
            save()
        elif msg == WM_LBUTTONDOWN:
            bump("msg_down")
            save()
        elif msg == WM_MOUSEWHEEL:
            bump("msg_wheel")
            save()
        elif msg == WM_KEYDOWN:
            bump("msg_keys")
            save()
        elif msg == WM_TIMER:  # как игры, которые опрашивают кнопки каждый кадр
            lb = bool(user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
            enter = bool(user32.GetAsyncKeyState(VK_RETURN) & 0x8000)
            if lb and not last["lb"]:
                bump("async_down")
            if enter and not last["enter"]:
                bump("async_enter")
            last["lb"], last["enter"] = lb, enter
            counts["foreground"] = int(user32.GetForegroundWindow() or 0) == int(hwnd)
            save()
            return 0
        elif msg == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        elif msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    wndproc = WNDPROC(proc)
    hinst = kernel32.GetModuleHandleW(None)
    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.lpfnWndProc = wndproc
    wc.hInstance = hinst
    wc.hbrBackground = wintypes.HBRUSH(COLOR_WINDOW + 1)
    wc.lpszClassName = "LexoPlayFakeGame"
    user32.RegisterClassExW(ctypes.byref(wc))
    x, y, w, h = (int(v) for v in rect.split(","))
    hwnd = user32.CreateWindowExW(0, "LexoPlayFakeGame", "LexoPlay fake game", WS_POPUP | WS_VISIBLE, x, y, w, h, None, None, hinst, None)
    if not hwnd:
        return 1
    devs = (RAWINPUTDEVICE * 2)(RAWINPUTDEVICE(0x01, 0x02, 0, hwnd), RAWINPUTDEVICE(0x01, 0x06, 0, hwnd))
    user32.RegisterRawInputDevices(devs, 2, ctypes.sizeof(RAWINPUTDEVICE))
    user32.SetTimer(hwnd, 1, 10, None)
    user32.SetForegroundWindow(hwnd)
    counts["hwnd"] = int(hwnd)
    save()
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    save()
    return 0
