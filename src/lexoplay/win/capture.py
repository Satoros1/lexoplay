"""Захват кадра игры.

* screen (основной) — снимок области экрана, где игра видна (mss/BitBlt). Совпадает с тем,
  что видит игрок: Magpie, DPI-масштабирование старых игр. Наши окна исключены из захвата
  (WDA_EXCLUDEFROMCAPTURE), так что строка перевода в снимок не попадёт.
* window (запасной) — PrintWindow(PW_RENDERFULLCONTENT): кадр самого окна, если в снимке
  экрана оно чёрное.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from PIL import Image

from lexoplay.geometry import Rect
from lexoplay.pipeline import CaptureError
from lexoplay.win import winapi

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

HDC = wintypes.HDC
user32.GetWindowDC.argtypes = [wintypes.HWND]
user32.GetWindowDC.restype = HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, HDC]
user32.PrintWindow.argtypes = [wintypes.HWND, HDC, wintypes.UINT]
gdi32.CreateCompatibleDC.argtypes = [HDC]
gdi32.CreateCompatibleDC.restype = HDC
gdi32.CreateCompatibleBitmap.argtypes = [HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [HDC]

PW_RENDERFULLCONTENT = 0x00000002
DIB_RGB_COLORS = 0
BI_RGB = 0


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


gdi32.GetDIBits.argtypes = [HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.POINTER(BITMAPINFOHEADER), wintypes.UINT]


def grab_window(hwnd: int) -> Image.Image:
    """Клиентская область окна через PrintWindow, в пикселях окна."""
    if not winapi.is_window(hwnd):
        raise CaptureError("окно игры закрыто")
    if winapi.is_minimized(hwnd):
        raise CaptureError("окно игры свёрнуто")
    wr = winapi.window_rect(hwnd)
    cr = winapi.client_rect_screen(hwnd)
    w, h = int(wr.w), int(wr.h)
    if w <= 0 or h <= 0 or cr.empty:
        raise CaptureError("у окна нулевой размер")

    win_dc = user32.GetWindowDC(hwnd)
    if not win_dc:
        raise CaptureError("GetWindowDC не сработал")
    mem_dc = gdi32.CreateCompatibleDC(win_dc)
    bmp = gdi32.CreateCompatibleBitmap(win_dc, w, h)
    old = gdi32.SelectObject(mem_dc, bmp)
    try:
        if not user32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT):
            raise CaptureError("PrintWindow не сработал")
        header = BITMAPINFOHEADER()
        header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        header.biWidth = w
        header.biHeight = -h  # сверху вниз
        header.biPlanes = 1
        header.biBitCount = 32
        header.biCompression = BI_RGB
        buf = ctypes.create_string_buffer(w * h * 4)
        if not gdi32.GetDIBits(mem_dc, bmp, 0, h, buf, ctypes.byref(header), DIB_RGB_COLORS):
            raise CaptureError("GetDIBits не сработал")
        img = Image.frombytes("RGB", (w, h), buf.raw, "raw", "BGRX")
    finally:
        gdi32.SelectObject(mem_dc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, win_dc)

    # окно рисуется в DC целиком (рамка + заголовок): вырезаем клиентскую часть
    ox, oy = int(cr.x - wr.x), int(cr.y - wr.y)
    box = (max(0, ox), max(0, oy), min(w, ox + int(cr.w)), min(h, oy + int(cr.h)))
    return img.crop(box)


def grab_screen(rect: Rect) -> Image.Image:
    """Снимок прямоугольника экрана (физические пиксели)."""
    import mss

    x, y, w, h = rect.to_int()
    if w <= 0 or h <= 0:
        raise CaptureError("пустая область экрана")
    with mss.mss() as sct:
        shot = sct.grab({"left": x, "top": y, "width": w, "height": h})
    return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
