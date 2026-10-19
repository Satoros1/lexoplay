"""Цвета и иконка. Иконка рисуется кодом, чтобы не возить файлы в exe."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

ACCENT = QColor("#3FC1A5")
ACCENT_DARK = QColor("#0F6E56")
STRIP_BG = QColor(10, 12, 16)
TEXT = QColor("#FFFFFF")
TEXT_MUTED = QColor("#9A988F")
NAME = QColor("#A59BEA")
FONT_FAMILY = "Segoe UI"


def draw_logo(size: int) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(ACCENT_DARK)
    p.setPen(Qt.PenStyle.NoPen)
    r = QRectF(0, 0, size, size)
    p.drawRoundedRect(r, size * 0.22, size * 0.22)
    f = QFont(FONT_FAMILY)
    f.setPixelSize(int(size * 0.62))
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor("#FFFFFF"))
    p.drawText(r, Qt.AlignmentFlag.AlignCenter, "L")
    p.end()
    return pm


def app_icon() -> QIcon:
    icon = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(draw_logo(s))
    return icon
