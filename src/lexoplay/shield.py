"""Что забирать у игры, пока открыт режим слов (F3, F7). Решение по одному событию, без WinAPI.

Сами хуки живут в win/llhooks.py, здесь только правила, чтобы их проверяли тесты:
- нажатие кнопки мыши или колесо над слоем слов забираем, пока режим открыт;
- клавишу режима (Esc, Enter, стрелки, R, 1–5) забираем, если Shift и Ctrl зажаты
  ровно как в её сочетании: так же решает и RegisterHotKey;
- отпускание забранной кнопки или клавиши тоже забираем, даже если режим уже закрылся:
  многие новеллы листают реплику по отпусканию;
- остальное (движение мыши, другие клавиши, клики мимо слоя) идёт как обычно.
"""

from __future__ import annotations

from dataclasses import dataclass, field

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MOUSEWHEEL = 0x020A
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
WM_MOUSEHWHEEL = 0x020E

VK_LBUTTON = 0x01
VK_RBUTTON = 0x02
VK_MBUTTON = 0x04
VK_XBUTTON1 = 0x05
VK_XBUTTON2 = 0x06

_DOWN = {WM_LBUTTONDOWN: VK_LBUTTON, WM_RBUTTONDOWN: VK_RBUTTON, WM_MBUTTONDOWN: VK_MBUTTON}
_UP = {WM_LBUTTONUP: VK_LBUTTON, WM_RBUTTONUP: VK_RBUTTON, WM_MBUTTONUP: VK_MBUTTON}
WHEEL = (WM_MOUSEWHEEL, WM_MOUSEHWHEEL)
KEY_DOWN = (WM_KEYDOWN, WM_SYSKEYDOWN)

Event = tuple[str, object]  # ("click", vk) левая/правая, ("button", vk) средняя/боковая, ("key", имя действия)


def button(msg: int, data: int = 0) -> tuple[int, bool] | None:
    """VK кнопки мыши и нажата ли она (False: отпущена). data: mouseData из хука (какая боковая)."""
    if msg in _DOWN:
        return _DOWN[msg], True
    if msg in _UP:
        return _UP[msg], False
    if msg in (WM_XBUTTONDOWN, WM_XBUTTONUP):
        which = (data >> 16) & 0xFFFF
        vk = VK_XBUTTON1 if which == 1 else VK_XBUTTON2 if which == 2 else 0
        return (vk, msg == WM_XBUTTONDOWN) if vk else None
    return None


def needs_target(msg: int) -> bool:
    """Для этого события надо знать, над слоем ли курсор: нажатия и колесо (отпускания решаются без этого)."""
    return msg in _DOWN or msg == WM_XBUTTONDOWN or msg in WHEEL


@dataclass
class Shield:
    active: bool = False  # режим слов открыт: новые нажатия забираем
    keys: dict[tuple[int, int], str] = field(default_factory=dict)  # (vk, модификаторы MOD_*) -> действие
    repeat: frozenset[str] = frozenset()  # действия, которые повторяются, пока клавишу держат (стрелки)
    held_buttons: set[int] = field(default_factory=set)  # нажатие забрали: отпускание тоже наше
    held_keys: set[int] = field(default_factory=set)

    @property
    def idle(self) -> bool:
        """Режим закрыт и ничего забранного не держат: хуки можно снимать."""
        return not self.active and not self.held_buttons and not self.held_keys

    def mouse(self, msg: int, data: int = 0, over_layer: bool = False) -> tuple[bool, Event | None]:
        """(забрать ли событие, что сообщить режиму слов)."""
        if msg in WHEEL:
            return self.active and over_layer, None  # колесо в новеллах листает текст
        b = button(msg, data)
        if b is None:
            return False, None
        vk, down = b
        if down:
            if self.active and over_layer:
                self.held_buttons.add(vk)
                return True, ("click" if vk in (VK_LBUTTON, VK_RBUTTON) else "button", vk)
            # мимо слоя или режим закрыт. Если кнопка числилась нашей, её отпускание мы пропустили:
            # теперь она игры, иначе мы забрали бы чужое отпускание и в игре кнопка «залипла» бы
            self.held_buttons.discard(vk)
            return False, None
        if vk in self.held_buttons:
            self.held_buttons.discard(vk)
            return True, None
        return False, None

    def key(self, msg: int, vk: int, mods: int = 0) -> tuple[bool, Event | None]:
        """(забрать ли событие, что сообщить режиму слов). mods: зажатые Ctrl/Shift/Alt/Win в битах MOD_*."""
        if msg not in KEY_DOWN:
            if vk in self.held_keys:
                self.held_keys.discard(vk)
                return True, None
            return False, None
        if vk in self.held_keys:
            # автоповтор зажатой клавиши. Если режим уже закрылся (например, этим же Esc), повтор
            # тоже наш: иначе игра получила бы Esc и открыла меню
            name = self.keys.get((vk, mods)) if self.active else None
            return True, (("key", name) if name is not None and name in self.repeat else None)
        if not self.active:
            return False, None
        name = self.keys.get((vk, mods))
        if name is None:
            return False, None
        self.held_keys.add(vk)
        return True, ("key", name)
