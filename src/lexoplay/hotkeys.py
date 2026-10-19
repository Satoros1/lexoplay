"""Разбор хоткеев из настроек ("Ctrl+Shift+F2") в модификаторы и VK-код Windows. Чистый модуль."""

from __future__ import annotations

from dataclasses import dataclass

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

_MODS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "shift": MOD_SHIFT, "alt": MOD_ALT, "win": MOD_WIN}

_KEYS: dict[str, int] = {f"f{i}": 0x6F + i for i in range(1, 25)}  # F1=0x70
_KEYS.update({chr(c).lower(): c for c in range(ord("A"), ord("Z") + 1)})
_KEYS.update({str(d): 0x30 + d for d in range(10)})
_KEYS.update(
    {
        "`": 0xC0,
        "tilde": 0xC0,
        "space": 0x20,
        "insert": 0x2D,
        "home": 0x24,
        "end": 0x23,
        "pageup": 0x21,
        "pagedown": 0x22,
        "pause": 0x13,
    }
)


@dataclass(frozen=True, slots=True)
class Hotkey:
    mods: int
    vk: int
    text: str


class HotkeyError(ValueError):
    pass


def parse(text: str) -> Hotkey:
    parts = [p.strip().lower() for p in text.replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise HotkeyError("пустой хоткей")
    *mods, key = parts
    m = 0
    for mod in mods:
        if mod not in _MODS:
            raise HotkeyError(f"неизвестный модификатор: {mod}")
        m |= _MODS[mod]
    if key not in _KEYS:
        raise HotkeyError(f"неизвестная клавиша: {key}")
    return Hotkey(m | MOD_NOREPEAT, _KEYS[key], text)
