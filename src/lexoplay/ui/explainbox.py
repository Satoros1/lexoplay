"""Общие куски окна LexoPlay для «Истории» и «Слов»: разбор реплики, кнопка в строке, подсветка поиска."""

from __future__ import annotations

import html
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from lexoplay.ui import theme
from lexoplay.ui.theme import icon
from lexoplay.ui.widgets import label

HIGHLIGHT = "#FBE3A0"  # найденное поиском


def row_tool(icon_name: str, tip: str, on_click: Callable[[], None], color: str = theme.MUTED, fill: str = "none") -> QToolButton:
    b = QToolButton()
    b.setObjectName("rowTool")
    b.setIcon(icon(icon_name, color, 15, fill=fill))
    b.setIconSize(QSize(15, 15))
    b.setToolTip(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.clicked.connect(on_click)
    return b



def explain_box(
    got: object, *, saved: Callable[[int], bool], on_save: Callable[[int, Any], None] | None
) -> QWidget:
    """Разбор реплики (как F7 в игре) в окне LexoPlay: в «Истории» и во вкладке «Слова».

    got: Explanation, текст ошибки или None, пока разбор грузится."""
    box = QFrame()
    box.setObjectName("explainBox")
    lay = QVBoxLayout(box)
    lay.setContentsMargins(12, 10, 12, 10)
    lay.setSpacing(6)
    if got is None:
        lay.addWidget(label("Разбираю реплику…", "muted"))
        return box
    if isinstance(got, str):
        lay.addWidget(label(got, "error", wrap=True))
        return box
    # сначала смысл реплики целиком, потом «дословно», потом трудные выражения
    if got.summary:  # type: ignore[attr-defined]
        lay.addWidget(label(got.summary, None, wrap=True))  # type: ignore[attr-defined]
    if got.literal:  # type: ignore[attr-defined]
        lay.addWidget(label(f"Дословно: {got.literal}", "muted", wrap=True))  # type: ignore[attr-defined]
    items = got.items  # type: ignore[attr-defined]
    for i, item in enumerate(items):
        row = QHBoxLayout()
        row.setSpacing(8)
        col = QVBoxLayout()
        col.setSpacing(1)
        head = label("", None, wrap=True)
        head.setTextFormat(Qt.TextFormat.RichText)
        base = f' <span style="color:{theme.MUTED}">· {html.escape(item.base)}</span>' if item.base else ""
        head.setText(f"<b>{html.escape(item.phrase)}</b>{base}: {html.escape(item.meaning)}")
        col.addWidget(head)
        if item.note:
            col.addWidget(label(item.note, "version", wrap=True))
        row.addLayout(col, 1)
        if saved(i):
            done = QLabel()
            done.setPixmap(icon("check", theme.GREEN, 16).pixmap(16, 16))
            done.setToolTip("Сохранено")
            row.addWidget(done, 0, Qt.AlignmentFlag.AlignTop)
        elif on_save is not None:
            row.addWidget(
                row_tool("plus", "Сохранить в слова", lambda i=i, item=item: on_save(i, item), theme.GREEN),
                0,
                Qt.AlignmentFlag.AlignTop,
            )
        lay.addLayout(row)
    if not items:
        lay.addWidget(label("Отдельных трудных выражений здесь нет.", "muted"))
    return box
