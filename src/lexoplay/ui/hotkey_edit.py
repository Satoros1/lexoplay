"""Поле для клавиши: нажал на него, нажал клавишу (или сочетание) — готово.

Esc отменяет ввод, Backspace/Delete очищает (если клавишу можно не задавать).
Одиночные Ctrl, Shift, Alt не принимаются: ждём основную клавишу.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFocusEvent, QKeyEvent
from PySide6.QtWidgets import QPushButton

from lexoplay.hotkeys import HotkeyError, normalize

_SPECIAL = {
    Qt.Key.Key_Insert: "Insert",
    Qt.Key.Key_Home: "Home",
    Qt.Key.Key_End: "End",
    Qt.Key.Key_PageUp: "PageUp",
    Qt.Key.Key_PageDown: "PageDown",
    Qt.Key.Key_Pause: "Pause",
    Qt.Key.Key_ScrollLock: "ScrollLock",
    Qt.Key.Key_Delete: "Delete",
    Qt.Key.Key_Space: "Space",
    Qt.Key.Key_QuoteLeft: "`",
}
_MODIFIER_KEYS = {
    Qt.Key.Key_Control,
    Qt.Key.Key_Shift,
    Qt.Key.Key_Alt,
    Qt.Key.Key_Meta,
    Qt.Key.Key_AltGr,
    Qt.Key.Key_CapsLock,
    Qt.Key.Key_NumLock,
}


def key_text(key: int, mods: Qt.KeyboardModifier) -> str | None:
    """Qt-клавиша → запись для настроек («Alt+F5»), или None, если такую не поддерживаем."""
    k = Qt.Key(key)
    name: str | None = None
    if Qt.Key.Key_F1 <= k <= Qt.Key.Key_F24:
        name = f"F{k - Qt.Key.Key_F1 + 1}"
    elif Qt.Key.Key_A <= k <= Qt.Key.Key_Z:
        name = chr(k)
    elif Qt.Key.Key_0 <= k <= Qt.Key.Key_9:
        digit = chr(k)
        name = f"Num{digit}" if mods & Qt.KeyboardModifier.KeypadModifier else digit
    elif k in _SPECIAL:
        name = _SPECIAL[k]
    if name is None:
        return None
    parts = []
    if mods & Qt.KeyboardModifier.ControlModifier:
        parts.append("Ctrl")
    if mods & Qt.KeyboardModifier.AltModifier:
        parts.append("Alt")
    if mods & Qt.KeyboardModifier.ShiftModifier:
        parts.append("Shift")
    if mods & Qt.KeyboardModifier.MetaModifier:
        parts.append("Win")
    try:
        return normalize("+".join([*parts, name]))
    except HotkeyError:
        return None


class HotkeyEdit(QPushButton):
    changed = Signal(str)

    def __init__(self, value: str, *, allow_empty: bool = False) -> None:
        super().__init__()
        self.setObjectName("hotkey")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._value = value
        self._allow_empty = allow_empty
        self._capturing = False
        self.clicked.connect(self._start)
        self._render()

    @property
    def value(self) -> str:
        return self._value

    def set_value(self, value: str) -> None:
        self._value = value
        self._render()

    def _render(self) -> None:
        self.setProperty("capturing", "true" if self._capturing else "false")
        if self._capturing:
            self.setText("Нажми клавишу…")
        else:
            self.setText(self._value or "не задано")
        self.style().unpolish(self)
        self.style().polish(self)

    def _start(self) -> None:
        self._capturing = True
        self.setFocus()
        self.grabKeyboard()
        self._render()

    def _stop(self) -> None:
        if self._capturing:
            self._capturing = False
            self.releaseKeyboard()
            self._render()

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        if not self._capturing:
            super().keyPressEvent(e)
            return
        key = e.key()
        if key in _MODIFIER_KEYS:
            return  # ждём основную клавишу
        if key == Qt.Key.Key_Escape:
            self._stop()
            return
        if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete) and not e.modifiers() and self._allow_empty:
            self._stop()
            self._set("")
            return
        text = key_text(key, e.modifiers())
        if text is None:
            self.setText("Эта клавиша не подойдёт")
            return
        self._stop()
        self._set(text)

    def _set(self, text: str) -> None:
        if text != self._value:
            self._value = text
            self._render()
            self.changed.emit(text)

    def focusOutEvent(self, e: QFocusEvent) -> None:  # noqa: N802
        self._stop()
        super().focusOutEvent(e)
