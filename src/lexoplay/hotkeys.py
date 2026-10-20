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
        "scrolllock": 0x91,
        "delete": 0x2E,
        # только для режима слов (в редакторе клавиш их не выбрать)
        "escape": 0x1B,
        "esc": 0x1B,
        "enter": 0x0D,
        "return": 0x0D,
        "left": 0x25,
        "up": 0x26,
        "right": 0x27,
        "down": 0x28,
    }
)
_KEYS.update({f"num{d}": 0x60 + d for d in range(10)})

# как писать клавишу в интерфейсе: f2 -> F2, pageup -> PageUp
_PRETTY = {"pageup": "PageUp", "pagedown": "PageDown", "scrolllock": "ScrollLock", "`": "`", "tilde": "`"}
_PRETTY_MODS = {MOD_CONTROL: "Ctrl", MOD_ALT: "Alt", MOD_SHIFT: "Shift", MOD_WIN: "Win"}


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


def key_name(key: str) -> str:
    k = key.lower()
    if k in _PRETTY:
        return _PRETTY[k]
    if k.startswith("num") and k[3:].isdigit():
        return f"Num{k[3:]}"
    if k.startswith("f") and k[1:].isdigit():
        return k.upper()
    return k.capitalize() if len(k) > 1 else k.upper()


def normalize(text: str) -> str:
    """Каноничная запись: «ctrl + f6» -> «Ctrl+F6». Пустая строка = клавиша не задана."""
    if not text.strip():
        return ""
    hk = parse(text)
    parts = [name for bit, name in _PRETTY_MODS.items() if hk.mods & bit]
    key = text.replace(" ", "").split("+")[-1]
    return "+".join([*parts, key_name(key)])


def risk(text: str) -> str | None:
    """Почему клавиша может мешать в игре, или None."""
    if not text:
        return None
    hk = parse(text)
    if hk.mods & (MOD_CONTROL | MOD_SHIFT):
        return "Ctrl и Shift в новеллах часто пропускают текст. Лучше отдельная F-клавиша."
    key = text.replace(" ", "").split("+")[-1].lower()
    if not hk.mods & ~MOD_NOREPEAT and (len(key) == 1 or key == "space"):
        return "Эта клавиша нужна игре или для ввода текста: пока игра на экране, она будет только у LexoPlay."
    return None
