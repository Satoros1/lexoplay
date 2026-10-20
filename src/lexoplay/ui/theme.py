"""Внешний вид: цвета, шрифт, стили окна, иконки. Всё рисуется кодом, файлы только шрифт.

Окно LexoPlay в духе макета «Lexo для игр»: тёплый светлый фон, белые карточки,
зелёный акцент Lexo, системный шрифт Segoe UI. Поверх игры (строка перевода, режим слов)
остаются тёмные полупрозрачные плашки, чтобы не спорить с игрой.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPainterPath, QPen, QPixmap

log = logging.getLogger(__name__)

# --- поверх игры ---
ACCENT = QColor("#3FC1A5")
ACCENT_DARK = QColor("#0F6E56")
STRIP_BG = QColor(10, 12, 16)
TEXT = QColor("#FFFFFF")
TEXT_MUTED = QColor("#9A988F")
NAME = QColor("#A59BEA")

# --- окно приложения ---
BG = "#F6F5F1"
SIDEBAR = "#EFEDE7"
CARD = "#FFFFFF"
BORDER = "#E2DFD7"
BORDER_STRONG = "#C9C6BC"
INK = "#1F1F1D"
MUTED = "#5F5D57"
GREEN = "#0F6E56"
GREEN_HOVER = "#0C5A47"
GREEN_SOFT = "#DCEFE8"
GREEN_INK = "#0F3D31"
WARN = "#8A6A1F"
WARN_SOFT = "#FBF1D9"
DANGER = "#B3261E"

FONT_FAMILY = "Segoe UI"  # обычный текст окна; load_fonts() выберет лучший из системных
DISPLAY_FAMILY = "Segoe UI"  # заголовки
_FONTS_LOADED = False


def assets_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "assets"


def load_fonts() -> str:
    """Системный шрифт Windows 11 (Segoe UI Variable), на Windows 10 обычный Segoe UI.

    Свои шрифты в exe не возим: у Windows хинтинг системных шрифтов лучший, текст
    получается чётким при любом масштабе экрана (100–200%). Manrope из 0.3 после
    обрезки вариативного шрифта потерял хинтинг и рисовался «с дырками».
    """
    global FONT_FAMILY, DISPLAY_FAMILY, _FONTS_LOADED
    if _FONTS_LOADED:
        return FONT_FAMILY
    _FONTS_LOADED = True
    families = set(QFontDatabase.families())
    for name in ("Segoe UI Variable Text", "Segoe UI"):
        if name in families:
            FONT_FAMILY = name
            break
    for name in ("Segoe UI Variable Display", FONT_FAMILY):
        if name in families:
            DISPLAY_FAMILY = name
            break
    log.info("шрифт окна: %s, заголовки: %s", FONT_FAMILY, DISPLAY_FAMILY)
    return FONT_FAMILY


def ui_font(size: float = 10.5, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    f = QFont(FONT_FAMILY)
    f.setPointSizeF(size)
    f.setWeight(weight)
    return f


def stylesheet() -> str:
    f = FONT_FAMILY
    d = DISPLAY_FAMILY
    return f"""
* {{ font-family: "{f}", "Segoe UI", sans-serif; color: {INK}; }}
QWidget#root, QWidget#page, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {BG}; }}
QWidget#sidebar {{ background: {SIDEBAR}; border-right: 1px solid {BORDER}; }}
QLabel {{ background: transparent; font-size: 10.5pt; }}
QLabel#h1 {{ font-family: "{d}", "Segoe UI"; font-size: 20pt; font-weight: 700; }}
QLabel#h2 {{ font-family: "{d}", "Segoe UI"; font-size: 13pt; font-weight: 700; }}
QLabel#section {{ font-size: 8.5pt; font-weight: 700; color: {MUTED}; letter-spacing: 1px; }}
QLabel#muted {{ color: {MUTED}; font-size: 10pt; }}
QLabel#title {{ font-size: 11.5pt; font-weight: 600; }}
QLabel#brand {{ font-family: "{d}", "Segoe UI"; font-size: 14pt; font-weight: 700; }}
QLabel#version {{ color: {MUTED}; font-size: 8.5pt; }}
QLabel#ok {{ color: {GREEN}; font-size: 9.5pt; font-weight: 600; }}
QLabel#warn {{ color: {WARN}; font-size: 9pt; }}
QLabel#error {{ color: {DANGER}; font-size: 9.5pt; }}
QLabel#chip {{ background: {GREEN_SOFT}; color: {GREEN_INK}; border-radius: 9px; padding: 2px 9px;
              font-size: 8.5pt; font-weight: 700; }}
QLabel#chipGray {{ background: #ECEAE4; color: {MUTED}; border-radius: 9px; padding: 2px 9px;
                  font-size: 8.5pt; font-weight: 700; }}
QLabel#keycap {{ background: {CARD}; border: 1px solid {BORDER_STRONG}; border-bottom-width: 2px;
                border-radius: 6px; padding: 1px 7px; font-weight: 700; font-size: 9pt; }}
QFrame#card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#cardActive {{ background: {CARD}; border: 2px solid {GREEN}; border-radius: 12px; }}
QFrame#banner {{ background: {WARN_SOFT}; border: 1px solid #EBD9A8; border-radius: 12px; }}
QFrame#row {{ background: transparent; border: none; border-bottom: 1px solid #EEECE6; }}
QFrame#rowLast {{ background: transparent; border: none; }}
QPushButton {{ font-size: 10pt; font-weight: 600; border-radius: 8px; padding: 8px 14px; min-height: 22px;
              background: transparent; border: 1px solid {BORDER_STRONG}; }}
QPushButton:hover {{ background: #EFEDE6; }}
QPushButton:pressed {{ background: #E6E3DA; }}
QPushButton:disabled {{ color: #A9A69D; border-color: {BORDER}; }}
QPushButton#primary {{ background: {GREEN}; color: white; border: 1px solid {GREEN}; }}
QPushButton#primary:hover {{ background: {GREEN_HOVER}; }}
QPushButton#primary:disabled {{ background: #9CC7B9; border-color: #9CC7B9; color: white; }}
QPushButton#danger {{ color: {DANGER}; }}
QPushButton#chipButton {{ border: none; border-radius: 11px; padding: 3px 10px; min-height: 16px; font-size: 9pt;
                         background: #ECEAE4; color: {INK}; }}
QPushButton#chipButton:hover {{ background: {GREEN_SOFT}; }}
QFrame#updateBar {{ background: {GREEN_SOFT}; border: none; border-bottom: 1px solid #BFE0D4; }}
QLabel#preview {{ background: #1E2433; border-radius: 6px; padding: 2px; }}
QPushButton#link {{ border: none; color: {GREEN}; padding: 2px 0; font-weight: 700; text-align: left; }}
QPushButton#link:hover {{ background: transparent; text-decoration: underline; }}
QPushButton#nav {{ text-align: left; border: none; border-radius: 8px; padding: 10px 12px; font-size: 10.5pt;
                  font-weight: 600; color: #4A4945; }}
QPushButton#nav:hover {{ background: #E6E3DB; }}
QPushButton#nav:checked {{ background: {GREEN_SOFT}; color: {GREEN_INK}; }}
QPushButton#hotkey {{ min-width: 92px; font-weight: 800; font-size: 10.5pt; background: {CARD};
                     border: 1px solid {BORDER_STRONG}; border-bottom-width: 2px; }}
QPushButton#hotkey[capturing="true"] {{ border: 2px solid {GREEN}; color: {GREEN}; }}
QToolButton {{ border: 1px solid {BORDER_STRONG}; border-radius: 8px; padding: 6px; background: transparent; }}
QToolButton:hover {{ background: #EFEDE6; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
QLineEdit {{ background: {CARD}; border: 1px solid {BORDER_STRONG}; border-radius: 8px; padding: 8px 10px;
            font-size: 10.5pt; selection-background-color: {GREEN_SOFT}; selection-color: {INK}; }}
QLineEdit:focus {{ border: 2px solid {GREEN}; padding: 7px 9px; }}
QLineEdit#code {{ font-family: "Consolas", monospace; font-size: 16pt; font-weight: 700; letter-spacing: 4px;
                 padding: 8px 12px; }}
QComboBox {{ background: {CARD}; border: 1px solid {BORDER_STRONG}; border-radius: 8px; padding: 6px 10px;
            font-size: 10pt; min-height: 22px; }}
QComboBox:hover {{ border-color: {GREEN}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {CARD}; border: 1px solid {BORDER}; selection-background-color: {GREEN_SOFT};
                              selection-color: {INK}; outline: 0; padding: 4px; }}
QCheckBox {{ font-size: 10.5pt; spacing: 8px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid {BORDER_STRONG}; background: {CARD}; }}
QCheckBox::indicator:checked {{ background: {GREEN}; border-color: {GREEN}; }}
QRadioButton {{ font-size: 10.5pt; font-weight: 700; spacing: 8px; }}
QSlider {{ min-height: 22px; background: transparent; }}
QSlider::groove:horizontal {{ height: 4px; background: #E4E1D8; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {GREEN}; border-radius: 2px; }}
QSlider::add-page:horizontal {{ background: #E4E1D8; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {CARD}; border: 1px solid #C9C5BA; width: 16px; height: 16px;
                             margin: -7px 0; border-radius: 9px; }}
QSlider::handle:horizontal:hover {{ border: 2px solid {GREEN}; width: 14px; height: 14px; }}
QLabel#statValue {{ font-family: "{d}", "Segoe UI"; font-size: 20pt; font-weight: 600; }}
QSpinBox {{ background: {CARD}; border: 1px solid {BORDER_STRONG}; border-radius: 8px; padding: 6px 8px; font-size: 10.5pt; }}
QSpinBox:focus {{ border: 2px solid {GREEN}; padding: 5px 7px; }}
QLabel#valueChip {{ background: #F1EFE9; border: 1px solid {BORDER}; border-radius: 7px; padding: 3px 0;
                   font-size: 9.5pt; font-weight: 700; color: {INK}; }}
QProgressBar {{ background: #ECEAE4; border: none; border-radius: 4px; height: 8px; max-height: 8px; }}
QProgressBar::chunk {{ background: {GREEN}; border-radius: 4px; }}
QListWidget {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 12px; padding: 4px; outline: 0; }}
QListWidget::item {{ border-radius: 8px; padding: 8px; margin: 1px 0; }}
QListWidget::item:selected {{ background: {GREEN_SOFT}; color: {INK}; }}
QListWidget::item:hover {{ background: #F3F1EC; }}
QMenu {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 7px 18px; border-radius: 6px; font-size: 10pt; }}
QMenu::item:selected {{ background: {GREEN_SOFT}; color: {INK}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #D6D3CA; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QToolTip {{ background: {INK}; color: white; border: none; padding: 6px 8px; border-radius: 6px; }}
QDialog {{ background: {BG}; }}
"""


# ------------------------------------------------------------------ иконки (контур 24×24, в духе Lucide, ISC)
_ICONS = {
    "games": '<path d="M6 11h4M8 9v4"/><path d="M15 12h.01M18 10h.01"/>'
    '<path d="M17.3 5H6.7a4 4 0 0 0-4 3.6C2.6 9.4 2 14.5 2 16a3 3 0 0 0 3 3c1 0 1.5-.5 2-1l1.4-1.4A2 2 0 0 1 9.8 16h4.4'
    'a2 2 0 0 1 1.4.6L17 18c.5.5 1 1 2 1a3 3 0 0 0 3-3c0-1.5-.6-6.6-.7-7.3A4 4 0 0 0 17.3 5z"/>',
    "lexo": '<path d="M12.8 2.2a2 2 0 0 0-1.6 0L2.6 6.1a1 1 0 0 0 0 1.8l8.6 3.9a2 2 0 0 0 1.6 0l8.6-3.9a1 1 0 0 0 0-1.8Z"/>'
    '<path d="m22 17.6-9.2 4.2a2 2 0 0 1-1.6 0L2 17.6"/><path d="m22 12.6-9.2 4.2a2 2 0 0 1-1.6 0L2 12.6"/>',
    "keys": '<rect width="20" height="16" x="2" y="4" rx="2"/><path d="M6 8h.01M10 8h.01M14 8h.01M18 8h.01'
    'M8 12h.01M12 12h.01M16 12h.01M7 16h10"/>',
    "view": '<path d="M2.1 12.3a1 1 0 0 1 0-.7 10.8 10.8 0 0 1 19.8 0 1 1 0 0 1 0 .7 10.8 10.8 0 0 1-19.8 0"/>'
    '<circle cx="12" cy="12" r="3"/>',
    "more": '<path d="M21 4h-7M10 4H3M21 12h-9M8 12H3M21 20h-5M12 20H3M14 2v4M8 10v4M16 18v4"/>',
    "plus": '<path d="M5 12h14M12 5v14"/>',
    "scan": '<path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2"/>'
    '<path d="M7 12h10"/>',
    "dots": '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
    "refresh": '<path d="M3 12a9 9 0 0 1 9-9 9.8 9.8 0 0 1 6.7 2.7L21 8"/><path d="M21 3v5h-5"/>'
    '<path d="M21 12a9 9 0 0 1-9 9 9.8 9.8 0 0 1-6.7-2.7L3 16"/><path d="M8 16H3v5"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "folder": '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.7-.9l-.8-1.2A2 2 0 0 0 7.9 3H4a2 2 0 0 0-2 2v13'
    'a2 2 0 0 0 2 2Z"/>',
    "send": '<path d="M14.5 21.7a.5.5 0 0 0 .9 0L22 2.6a.5.5 0 0 0-.6-.6L2.3 8.5a.5.5 0 0 0 0 .9l8 3.2a2 2 0 0 1 1.1 1.1z"/>'
    '<path d="m21.9 2.1-10.9 10.9"/>',
    "external": '<path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
    "link": '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.8 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7'
    'l1.7-1.7"/>',
    "history": '<path d="M3 12a9 9 0 1 0 9-9 9.8 9.8 0 0 0-6.7 2.7L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l4 2"/>',
    "words": '<path d="M12 7v14"/><path d="M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13'
    'a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z"/>',
    "copy": '<rect width="14" height="14" x="8" y="8" rx="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
    "trash": '<path d="M3 6h18M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "progress": '<path d="M3 3v16a2 2 0 0 0 2 2h16"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/>',
}


def icon_svg(name: str, color: str = INK, size: int = 24, stroke: float = 2.0) -> str:
    body = _ICONS[name]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    )


def icon(name: str, color: str = INK, size: int = 18) -> QIcon:
    from PySide6.QtSvg import QSvgRenderer

    out = QIcon()
    for scale in (1, 2):
        px = size * scale
        pm = QPixmap(px, px)
        pm.fill(Qt.GlobalColor.transparent)
        renderer = QSvgRenderer(QByteArray(icon_svg(name, color).encode()))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(p, QRectF(0, 0, px, px))
        p.end()
        pm.setDevicePixelRatio(scale)
        out.addPixmap(pm)
    return out


# ------------------------------------------------------------------ логотип
def draw_logo(size: int) -> QPixmap:
    """Зелёный квадрат Lexo и белое облачко реплики с двумя строками: оригинал и перевод."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = float(size)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(ACCENT_DARK)
    p.drawRoundedRect(QRectF(0, 0, s, s), s * 0.23, s * 0.23)

    # облачко реплики
    bx, by, bw, bh = s * 0.17, s * 0.2, s * 0.66, s * 0.47
    bubble = QPainterPath()
    bubble.addRoundedRect(QRectF(bx, by, bw, bh), s * 0.12, s * 0.12)
    tail = QPainterPath()
    tail.moveTo(QPointF(bx + bw * 0.2, by + bh - s * 0.02))
    tail.lineTo(QPointF(bx + bw * 0.14, by + bh + s * 0.15))
    tail.lineTo(QPointF(bx + bw * 0.44, by + bh - s * 0.02))
    tail.closeSubpath()
    p.setBrush(QColor("#FFFFFF"))
    p.drawPath(bubble.united(tail))

    # две строки: верхняя тёмная (оригинал), нижняя зелёная (перевод)
    if size >= 20:
        lw = max(1.0, s * 0.075)
        pen = QPen(QColor("#13261F"), lw, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        y1 = by + bh * 0.36
        p.drawLine(QPointF(bx + bw * 0.2, y1), QPointF(bx + bw * 0.8, y1))
        pen.setColor(ACCENT)
        p.setPen(pen)
        y2 = by + bh * 0.66
        p.drawLine(QPointF(bx + bw * 0.2, y2), QPointF(bx + bw * 0.58, y2))
    else:
        p.setBrush(ACCENT_DARK)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(bx + bw * 0.22, by + bh * 0.42, bw * 0.56, bh * 0.2), 1, 1)
    p.end()
    return pm


def app_icon() -> QIcon:
    icon_ = QIcon()
    for s in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        icon_.addPixmap(draw_logo(s))
    return icon_
