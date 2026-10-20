"""Мелкие детали окна LexoPlay: подписи, карточки, кнопки, квадратики игр, страница с прокруткой."""

from __future__ import annotations

import hashlib

from PySide6.QtCore import QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QAbstractButton,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from lexoplay.ui import theme
from lexoplay.ui.theme import icon


def label(text: str, name: str | None = None, *, wrap: bool = False) -> QLabel:
    lbl = QLabel(text)
    if name:
        lbl.setObjectName(name)
    lbl.setWordWrap(wrap)
    return lbl


def card(name: str = "card") -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName(name)
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(18, 16, 18, 16)
    lay.setSpacing(10)
    return frame, lay


def button(text: str, kind: str | None = None, icon_name: str | None = None) -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    if icon_name:
        b.setIcon(icon(icon_name, "#FFFFFF" if kind == "primary" else theme.INK, 16))
        b.setIconSize(QSize(16, 16))
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def keycap(text: str) -> QLabel:
    return label(text or "нет", "keycap")


def clear(layout) -> None:  # type: ignore[no-untyped-def]
    """Убрать всё из раскладки сразу: deleteLater сработает позже, а виджет до этого висел бы поверх."""
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.hide()
            w.setParent(None)
            w.deleteLater()
        elif item.layout() is not None:
            clear(item.layout())


def tile_color(seed: str) -> QColor:
    palette = ["#59506F", "#3D5A80", "#8A5A44", "#2F6F62", "#7A4E7E", "#4B6584", "#806A2F"]
    h = int(hashlib.md5(seed.encode()).hexdigest(), 16)
    return QColor(palette[h % len(palette)])


class Tile(QWidget):
    """Квадратик игры с первой буквой названия."""

    def __init__(self, title: str, seed: str, size: int = 44) -> None:
        super().__init__()
        self._title, self._seed = title, seed
        self.setFixedSize(size, size)

    def paintEvent(self, _e) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(tile_color(self._seed))
        p.drawRoundedRect(QRectF(self.rect()), 10, 10)
        f = theme.ui_font(14, QFont.Weight.Bold)
        p.setFont(f)
        p.setPen(QColor("#FFFFFF"))
        letter = next((c for c in self._title if c.isalnum()), "?").upper()
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, letter)
        p.end()


def page_shell(title: str, subtitle: str) -> tuple[QScrollArea, QVBoxLayout, QHBoxLayout]:
    """Страница с прокруткой: заголовок, подзаголовок, место под кнопки справа и тело."""
    inner = QWidget()
    inner.setObjectName("page")
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(32, 28, 32, 28)
    lay.setSpacing(16)
    head = QHBoxLayout()
    col = QVBoxLayout()
    col.setSpacing(2)
    col.addWidget(label(title, "h1"))
    col.addWidget(label(subtitle, "muted", wrap=True))
    head.addLayout(col, 1)
    actions = QHBoxLayout()
    actions.setSpacing(8)
    head.addLayout(actions)
    lay.addLayout(head)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidget(inner)
    return scroll, lay, actions


class Toggle(QCheckBox):
    """Переключатель-«таблетка» вместо квадратной галочки. Это QCheckBox: toggled, setChecked работают как обычно."""

    W, H = 42, 24

    def __init__(self, checked: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(self.W, self.H)
        self._pos = 1.0 if checked else 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(130)
        self._anim.valueChanged.connect(self._moved)
        self.toggled.connect(self._animate)

    def _moved(self, value: object) -> None:
        self._pos = float(value)  # type: ignore[arg-type]
        self.update()

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.W, self.H)

    def hitButton(self, pos) -> bool:  # type: ignore[no-untyped-def]  # noqa: N802
        return self.rect().contains(pos)

    def paintEvent(self, _e) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(0.5, 0.5, self.W - 1, self.H - 1)
        off, on = QColor("#D6D2C8"), QColor(theme.GREEN)
        t = self._pos
        track = QColor(
            round(off.red() + (on.red() - off.red()) * t),
            round(off.green() + (on.green() - off.green()) * t),
            round(off.blue() + (on.blue() - off.blue()) * t),
        )
        if not self.isEnabled():
            track.setAlphaF(0.45)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        knob = self.H - 6.0
        x = 3 + (self.W - 6 - knob) * t
        p.setBrush(QColor(0, 0, 0, 28))
        p.drawEllipse(QRectF(x, 4, knob, knob))  # мягкая тень
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QRectF(x, 3, knob, knob))
        p.end()


def setting_row(title: str, desc: str, control: QWidget, *, last: bool = False) -> QFrame:
    """Строка настройки: слева название и пояснение, справа переключатель или ползунок."""
    row = QFrame()
    row.setObjectName("rowLast" if last else "row")
    lay = QHBoxLayout(row)
    lay.setContentsMargins(18, 14, 18, 14)
    lay.setSpacing(16)
    col = QVBoxLayout()
    col.setSpacing(2)
    col.addWidget(label(title, "title"))
    if desc:
        col.addWidget(label(desc, "muted", wrap=True))
    lay.addLayout(col, 1)
    lay.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
    if isinstance(control, QAbstractButton):
        # клик по всей строке тоже переключает
        row.mouseReleaseEvent = lambda e, c=control: c.toggle() if c.isEnabled() else None  # type: ignore[method-assign]
        row.setCursor(Qt.CursorShape.PointingHandCursor)
    return row


class ValueSlider(QWidget):
    """Ползунок со значением в аккуратной плашке справа."""

    def __init__(self, lo: int, hi: int, value: int, suffix: str = "%", width: int = 240) -> None:
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(lo, hi)
        self.slider.setValue(value)
        self.slider.setFixedWidth(width)
        self.slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.value = label("", "valueChip")
        self.value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.value.setFixedWidth(54)
        self._suffix = suffix
        lay.addWidget(self.slider)
        lay.addWidget(self.value)
        self.slider.valueChanged.connect(self._show)
        self._show(value)

    def _show(self, v: int) -> None:
        self.value.setText(f"{v}{self._suffix}")


def card_list() -> tuple[QFrame, QVBoxLayout]:
    """Карточка под строки настроек: без внутренних отступов, строки сами с разделителями."""
    frame = QFrame()
    frame.setObjectName("card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(0, 2, 0, 2)
    lay.setSpacing(0)
    return frame, lay
