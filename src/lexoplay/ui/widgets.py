"""Мелкие детали окна LexoPlay: подписи, карточки, кнопки, квадратики игр, страница с прокруткой."""

from __future__ import annotations

import hashlib

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

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
