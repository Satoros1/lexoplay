"""Внешний вид: цвета, шрифт, стили окна, иконки. Всё рисуется кодом, файлы только шрифт.

Окно LexoPlay в духе макета «Lexo для игр»: тёплый светлый фон, белые карточки,
зелёный акцент Lexo, системный шрифт Segoe UI. Поверх игры (строка перевода, режим слов)
остаются тёмные полупрозрачные плашки, чтобы не спорить с игрой.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QByteArray, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPainterPath, QPen, QPixmap

log = logging.getLogger(__name__)

# --- поверх игры ---
ACCENT = QColor("#3FC1A5")
ACCENT_DARK = QColor("#0F6E56")
STRIP_BG = QColor(18, 17, 15)  # тёплый тёмный, как тёмная тема окна
TEXT = QColor("#FFFFFF")
TEXT_MUTED = QColor("#AAA398")
NAME = QColor("#A59BEA")
KNOWN = QColor("#F2C94C")  # твои слова: из Lexo и сохранённые
RARE = QColor("#FF7A9C")  # трудные (редкие) слова в режиме слов

# --- окно приложения ---
# Две палитры из одной тёплой гаммы: светлая «бумага» и тёмная в тон плашкам поверх игры.
# Модуль держит текущие значения в своих именах (theme.BG, theme.INK...): set_mode() их подменяет,
# поэтому окно читает цвета в момент сборки, а не при импорте.
LIGHT = {
    "BG": "#F6F5F1",
    "SIDEBAR": "#EFEDE7",
    "CARD": "#FFFFFF",
    "RAISED": "#F7F6F2",  # вложенный блок внутри карточки
    "SUNKEN": "#ECEAE4",  # дорожки, серые плашки, пустые клетки
    "HOVER": "#EFEDE6",
    "PRESSED": "#E6E3DA",
    "HAIR": "#EEECE6",  # тонкая линия между строками
    "BORDER": "#E2DFD7",
    "BORDER_STRONG": "#C9C6BC",
    "INK": "#1F1F1D",
    "MUTED": "#5F5D57",
    "FAINT": "#6B685F",  # подписи и мета
    "DISABLED": "#A9A69D",
    "GREEN": "#0F6E56",
    "GREEN_HOVER": "#0C5A47",
    "GREEN_SOFT": "#DCEFE8",
    "GREEN_LINE": "#BFE0D4",
    "GREEN_INK": "#0F3D31",
    "GREEN_OFF": "#9CC7B9",  # выключенная главная кнопка
    "ON_GREEN": "#FFFFFF",  # текст на зелёном
    "ON_GREEN_OFF": "#FFFFFF",
    "WARN": "#8A6A1F",
    "WARN_SOFT": "#FBF1D9",
    "WARN_LINE": "#EBD9A8",
    "DANGER": "#B3261E",
    "INFO": "#2B6CB0",
    "STAR": "#D99A1E",
    "MARK": "#FBE3A0",  # найденное поиском
    "BAR": "#D5D1C6",  # столбцы графика, кроме сегодняшнего
    "TILE": "#E6E3DA",  # квадратик игры
    "TILE_INK": "#3F3D38",
    "KNOB": "#FFFFFF",
    "SWITCH_OFF": "#D6D2C8",
    "SCROLL": "#D6D3CA",
    "SCROLL_HOVER": "#BDB9AE",
    "TIP_BG": "#1F1F1D",
    "TIP_INK": "#FFFFFF",
    "HEAT": ("#CFE8DE", "#9DD1BD", "#4FA788", "#0F6E56"),  # календарь: от «чуть-чуть» до «много»
}
DARK = {
    "BG": "#161513",
    "SIDEBAR": "#12110F",
    "CARD": "#1C1A18",
    "RAISED": "#232120",
    "SUNKEN": "#262421",
    "HOVER": "#262421",
    "PRESSED": "#2E2B28",
    "HAIR": "#262422",
    "BORDER": "#2B2927",
    "BORDER_STRONG": "#3D3A36",
    "INK": "#EFE9DE",
    "MUTED": "#AAA398",
    "FAINT": "#8A847A",
    "DISABLED": "#5F5B54",
    "GREEN": "#3FC1A5",  # тот же зелёный, что на плашках поверх игры
    "GREEN_HOVER": "#56CDB3",
    "GREEN_SOFT": "#17302A",
    "GREEN_LINE": "#24493F",
    "GREEN_INK": "#8FE0CB",
    "GREEN_OFF": "#24493F",
    "ON_GREEN": "#0B2A22",
    "ON_GREEN_OFF": "#7FA599",
    "WARN": "#E0B95A",
    "WARN_SOFT": "#2B2416",
    "WARN_LINE": "#4A3D1F",
    "DANGER": "#F0716F",
    "INFO": "#7FB2E5",
    "STAR": "#E3AE3D",
    "MARK": "#5C4A14",
    "BAR": "#3D3A36",
    "TILE": "#2E2B28",
    "TILE_INK": "#EFE9DE",
    "KNOB": "#EFE9DE",
    "SWITCH_OFF": "#3D3A36",
    "SCROLL": "#3D3A36",
    "SCROLL_HOVER": "#55514B",
    "TIP_BG": "#EFE9DE",
    "TIP_INK": "#161513",
    "HEAT": ("#1D3A32", "#256152", "#31957D", "#3FC1A5"),
}
MODES = {"light": LIGHT, "dark": DARK}
MODE = "light"
BG: str = LIGHT["BG"]  # type: ignore[assignment]
SIDEBAR: str = LIGHT["SIDEBAR"]  # type: ignore[assignment]
CARD: str = LIGHT["CARD"]  # type: ignore[assignment]
RAISED: str = LIGHT["RAISED"]  # type: ignore[assignment]
SUNKEN: str = LIGHT["SUNKEN"]  # type: ignore[assignment]
HOVER: str = LIGHT["HOVER"]  # type: ignore[assignment]
PRESSED: str = LIGHT["PRESSED"]  # type: ignore[assignment]
HAIR: str = LIGHT["HAIR"]  # type: ignore[assignment]
BORDER: str = LIGHT["BORDER"]  # type: ignore[assignment]
BORDER_STRONG: str = LIGHT["BORDER_STRONG"]  # type: ignore[assignment]
INK: str = LIGHT["INK"]  # type: ignore[assignment]
MUTED: str = LIGHT["MUTED"]  # type: ignore[assignment]
FAINT: str = LIGHT["FAINT"]  # type: ignore[assignment]
DISABLED: str = LIGHT["DISABLED"]  # type: ignore[assignment]
GREEN: str = LIGHT["GREEN"]  # type: ignore[assignment]
GREEN_HOVER: str = LIGHT["GREEN_HOVER"]  # type: ignore[assignment]
GREEN_SOFT: str = LIGHT["GREEN_SOFT"]  # type: ignore[assignment]
GREEN_LINE: str = LIGHT["GREEN_LINE"]  # type: ignore[assignment]
GREEN_INK: str = LIGHT["GREEN_INK"]  # type: ignore[assignment]
GREEN_OFF: str = LIGHT["GREEN_OFF"]  # type: ignore[assignment]
ON_GREEN: str = LIGHT["ON_GREEN"]  # type: ignore[assignment]
ON_GREEN_OFF: str = LIGHT["ON_GREEN_OFF"]  # type: ignore[assignment]
WARN: str = LIGHT["WARN"]  # type: ignore[assignment]
WARN_SOFT: str = LIGHT["WARN_SOFT"]  # type: ignore[assignment]
WARN_LINE: str = LIGHT["WARN_LINE"]  # type: ignore[assignment]
DANGER: str = LIGHT["DANGER"]  # type: ignore[assignment]
INFO: str = LIGHT["INFO"]  # type: ignore[assignment]
STAR: str = LIGHT["STAR"]  # type: ignore[assignment]
MARK: str = LIGHT["MARK"]  # type: ignore[assignment]
BAR: str = LIGHT["BAR"]  # type: ignore[assignment]
TILE: str = LIGHT["TILE"]  # type: ignore[assignment]
TILE_INK: str = LIGHT["TILE_INK"]  # type: ignore[assignment]
KNOB: str = LIGHT["KNOB"]  # type: ignore[assignment]
SWITCH_OFF: str = LIGHT["SWITCH_OFF"]  # type: ignore[assignment]
SCROLL: str = LIGHT["SCROLL"]  # type: ignore[assignment]
SCROLL_HOVER: str = LIGHT["SCROLL_HOVER"]  # type: ignore[assignment]
TIP_BG: str = LIGHT["TIP_BG"]  # type: ignore[assignment]
TIP_INK: str = LIGHT["TIP_INK"]  # type: ignore[assignment]
HEAT: tuple[str, ...] = LIGHT["HEAT"]  # type: ignore[assignment]


def set_mode(mode: str) -> str:
    """Выбрать палитру окна. Вызывать до сборки окна: готовые виджеты сами не перекрасятся."""
    global MODE
    MODE = mode if mode in MODES else "light"
    globals().update(MODES[MODE])
    return MODE


def is_dark() -> bool:
    return MODE == "dark"


def apply_palette(app) -> None:  # type: ignore[no-untyped-def]
    """Палитра Qt под тему: по ней стиль Windows рисует то, до чего не дотягивается таблица стилей
    (стрелки списков, системные окна вопросов, выделение текста)."""
    from PySide6.QtGui import QPalette

    pal = QPalette()
    role = QPalette.ColorRole
    for r, c in (
        (role.Window, BG), (role.Base, CARD), (role.AlternateBase, RAISED), (role.Button, CARD),
        (role.WindowText, INK), (role.Text, INK), (role.ButtonText, INK), (role.BrightText, INK),
        (role.PlaceholderText, FAINT), (role.Highlight, GREEN_SOFT), (role.HighlightedText, INK),
        (role.ToolTipBase, TIP_BG), (role.ToolTipText, TIP_INK), (role.Link, GREEN),
        (role.Light, BORDER), (role.Midlight, BORDER), (role.Mid, BORDER_STRONG), (role.Dark, BORDER_STRONG),
    ):  # fmt: skip
        pal.setColor(r, QColor(c))
    for r in (role.WindowText, role.Text, role.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, r, QColor(DISABLED))
    app.setPalette(pal)


def dark_titlebar(widget) -> None:  # type: ignore[no-untyped-def]
    """Заголовок окна Windows в цвет темы (Windows 10 2004 и новее). В других системах ничего не делает."""
    import sys

    if sys.platform != "win32":
        return
    try:
        import ctypes

        value = ctypes.c_int(1 if is_dark() else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(  # 20 = DWMWA_USE_IMMERSIVE_DARK_MODE
            ctypes.c_void_p(int(widget.winId())), ctypes.c_uint(20), ctypes.byref(value), ctypes.c_uint(ctypes.sizeof(value))
        )
    except Exception:  # noqa: BLE001
        log.debug("заголовок окна под тему не покрасился", exc_info=True)


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


def corner(h: float) -> float:
    """Скругление плашек и кнопок поверх игры по их высоте: как у кнопок окна (8 при высоте 36), а не «таблетка»."""
    return min(10.0, h * 0.28)


def ui_font(size: float = 10.5, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    f = QFont(FONT_FAMILY)
    f.setPointSizeF(size)
    f.setWeight(weight)
    return f


def _asset_url(name: str, color: str, size: int) -> str:
    """Значок для таблицы стилей: Qt берёт стрелку списка и галочку только из файла, поэтому рисуем их
    цветом темы во временную папку (обычный размер и @2x для чёткости на 150–200%). Не вышло: без значка."""
    import tempfile

    try:
        folder = Path(tempfile.gettempdir()) / "lexoplay-ui"
        folder.mkdir(parents=True, exist_ok=True)
        base = folder / f"{name}-{color.lstrip('#')}-{size}.png"
        if not base.exists():
            src = icon(name, color, size)
            src.pixmap(QSize(size, size), 1.0).save(str(base))
            src.pixmap(QSize(size, size), 2.0).save(str(base.with_name(base.stem + "@2x.png")))
        return base.as_posix()
    except Exception:  # noqa: BLE001
        log.debug("значок %s для стилей не нарисовался", name, exc_info=True)
        return ""


def stylesheet() -> str:
    """Стили окна. Жирность: обычный текст 400, заголовки и главное действие 600, больше ничего.
    Размеры на сетке 4 px: кнопка и строка меню 36, поле 36, скругления 6 / 8 / 12."""
    f = FONT_FAMILY
    d = DISPLAY_FAMILY
    arrow = _asset_url("chevron-down", MUTED, 12)
    arrow_rule = f"QComboBox::down-arrow {{ image: url({arrow}); width: 12px; height: 12px; }}" if arrow else ""
    check = _asset_url("check", ON_GREEN, 14)
    check_rule = f"QCheckBox::indicator:checked {{ image: url({check}); }}" if check else ""
    return f"""
* {{ font-family: "{f}", "Segoe UI", sans-serif; color: {INK}; }}
QWidget#root, QWidget#page, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {BG}; }}
QWidget#sidebar {{ background: {SIDEBAR}; border-right: 1px solid {BORDER}; }}
QLabel {{ background: transparent; font-size: 10.5pt; }}
QLabel#h1 {{ font-family: "{d}", "Segoe UI"; font-size: 20pt; font-weight: 600; }}
QLabel#h2 {{ font-family: "{d}", "Segoe UI"; font-size: 13pt; font-weight: 600; }}
QLabel#section {{ font-size: 9.5pt; font-weight: 600; color: {MUTED}; }}
QLabel#muted {{ color: {MUTED}; font-size: 10pt; }}
QLabel#title {{ font-size: 11.5pt; font-weight: 600; }}
QLabel#rowTitle {{ font-size: 10.5pt; }}
QLabel#brand {{ font-family: "{d}", "Segoe UI"; font-size: 13pt; font-weight: 600; }}
QLabel#version {{ color: {FAINT}; font-size: 8.5pt; }}
QLabel#ok {{ color: {GREEN}; font-size: 9.5pt; font-weight: 600; }}
QLabel#warn {{ color: {WARN}; font-size: 9pt; }}
QLabel#error {{ color: {DANGER}; font-size: 9.5pt; }}
QLabel#chip {{ background: {GREEN_SOFT}; color: {GREEN_INK}; border-radius: 6px; padding: 2px 8px;
              font-size: 9pt; }}
QLabel#chipGray {{ background: {SUNKEN}; color: {MUTED}; border-radius: 6px; padding: 2px 8px;
                  font-size: 9pt; }}
QLabel#keycap {{ background: {CARD}; border: 1px solid {BORDER_STRONG}; border-bottom-width: 2px;
                border-radius: 6px; padding: 1px 7px; font-weight: 600; font-size: 9pt; }}
QFrame#card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#cardActive {{ background: {CARD}; border: 1px solid {GREEN}; border-radius: 12px; }}
QFrame#inset {{ background: {RAISED}; border: 1px solid {HAIR}; border-radius: 8px; }}
QFrame#banner {{ background: {WARN_SOFT}; border: 1px solid {WARN_LINE}; border-radius: 12px; }}
QFrame#row {{ background: transparent; border: none; border-bottom: 1px solid {HAIR}; }}
QFrame#rowLast {{ background: transparent; border: none; }}
QPushButton {{ font-size: 10pt; border-radius: 8px; padding: 6px 14px; min-height: 22px;
              background: transparent; border: 1px solid {BORDER_STRONG}; }}
QPushButton:hover {{ background: {HOVER}; }}
QPushButton:pressed {{ background: {PRESSED}; }}
QPushButton:disabled {{ color: {DISABLED}; border-color: {BORDER}; }}
QPushButton:focus {{ border-color: {GREEN}; }}
QPushButton#primary {{ background: {GREEN}; color: {ON_GREEN}; border: 1px solid {GREEN}; font-weight: 600; }}
QPushButton#primary:hover {{ background: {GREEN_HOVER}; border-color: {GREEN_HOVER}; }}
QPushButton#primary:disabled {{ background: {GREEN_OFF}; border-color: {GREEN_OFF}; color: {ON_GREEN_OFF}; }}
QPushButton#danger {{ color: {DANGER}; }}
QPushButton#chipButton {{ border: none; border-radius: 6px; padding: 2px 10px; min-height: 20px; font-size: 9pt;
                         background: {SUNKEN}; color: {INK}; }}
QPushButton#chipButton:hover {{ background: {GREEN_SOFT}; }}
QPushButton#filterChip {{ border: 1px solid {BORDER_STRONG}; border-radius: 8px; padding: 6px 12px; min-height: 22px;
                         background: {CARD}; }}
QPushButton#filterChip:checked {{ background: {GREEN_SOFT}; border-color: {GREEN}; color: {GREEN_INK}; }}
QTabBar::tab {{ border: 1px solid {BORDER_STRONG}; border-radius: 8px; padding: 6px 12px; margin-right: 8px;
               background: {CARD}; font-size: 10pt; color: {INK}; }}
QTabBar::tab:selected {{ background: {GREEN_SOFT}; border-color: {GREEN}; color: {GREEN_INK}; }}
QTabBar::tab:hover:!selected {{ background: {HOVER}; }}
QTabBar::tab:disabled {{ color: {DISABLED}; border-color: {BORDER}; }}
QPushButton#segment {{ border: none; border-radius: 6px; padding: 4px 12px; min-height: 16px; font-size: 9pt;
                      background: transparent; color: {MUTED}; }}
QPushButton#segment:hover {{ background: {HOVER}; }}
QPushButton#segment:checked {{ background: {CARD}; color: {INK}; border: 1px solid {BORDER}; }}
QFrame#segments {{ background: {SUNKEN}; border: none; border-radius: 8px; }}
QFrame#sessionHead {{ background: transparent; border: none; }}
QFrame#sessionHead:hover {{ background: {RAISED}; border-radius: 12px; }}
QFrame#sessionBody {{ background: transparent; border: none; border-top: 1px solid {HAIR}; }}
QFrame#explainBox {{ background: {RAISED}; border: 1px solid {HAIR}; border-radius: 8px; }}
QLabel#wordChip {{ background: {GREEN_SOFT}; color: {GREEN_INK}; border-radius: 6px; padding: 2px 8px;
                  font-size: 9pt; }}
QLabel#recordValue {{ font-size: 11.5pt; font-weight: 600; }}
QFrame#updateBar {{ background: {GREEN_SOFT}; border: none; border-bottom: 1px solid {GREEN_LINE}; }}
QLabel#updateText {{ color: {GREEN_INK}; font-size: 10pt; }}
QLabel#preview {{ background: #12110F; border: 1px solid {BORDER}; border-radius: 6px; padding: 2px; }}
QPushButton#link {{ border: none; color: {GREEN}; padding: 2px 0; font-weight: 600; text-align: left; }}
QPushButton#link:hover {{ background: transparent; text-decoration: underline; }}
QPushButton#nav {{ text-align: left; border: none; border-radius: 8px; padding: 7px 12px; font-size: 10.5pt;
                  color: {MUTED}; }}
QPushButton#nav:hover {{ background: {HOVER}; color: {INK}; }}
QPushButton#nav:checked {{ background: transparent; color: {GREEN_INK}; font-weight: 600; }}
QPushButton#nav:checked:hover {{ background: transparent; }}
QFrame#navPill {{ background: {GREEN_SOFT}; border-radius: 8px; }}
QPushButton#hotkey {{ min-width: 92px; font-weight: 600; font-size: 10.5pt; background: {CARD};
                     border: 1px solid {BORDER_STRONG}; border-bottom-width: 2px; }}
QPushButton#hotkey[capturing="true"] {{ border: 2px solid {GREEN}; color: {GREEN}; }}
QToolButton {{ border: 1px solid {BORDER_STRONG}; border-radius: 8px; padding: 6px; background: transparent; }}
QToolButton:hover {{ background: {HOVER}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
QToolButton#rowTool {{ border: none; padding: 4px; border-radius: 6px; }}
QToolButton#rowTool:hover {{ background: {HOVER}; }}
QLineEdit {{ background: {CARD}; border: 1px solid {BORDER_STRONG}; border-radius: 8px; padding: 7px 10px;
            font-size: 10.5pt; selection-background-color: {GREEN_SOFT}; selection-color: {INK}; }}
QLineEdit:focus {{ border: 2px solid {GREEN}; padding: 6px 9px; }}
QPlainTextEdit {{ background: {CARD}; border: 1px solid {BORDER_STRONG}; border-radius: 8px; padding: 6px 8px;
                 font-size: 10pt; selection-background-color: {GREEN_SOFT}; selection-color: {INK}; }}
QPlainTextEdit:focus {{ border: 2px solid {GREEN}; padding: 5px 7px; }}
QLineEdit#code {{ font-family: "Consolas", monospace; font-size: 16pt; font-weight: 600; letter-spacing: 4px;
                 padding: 8px 12px; }}
QComboBox {{ background: {CARD}; border: 1px solid {BORDER_STRONG}; border-radius: 8px; padding: 6px 10px;
            font-size: 10pt; min-height: 22px; }}
QComboBox:hover {{ border-color: {GREEN}; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
{arrow_rule}
QComboBox QAbstractItemView {{ background: {CARD}; border: 1px solid {BORDER}; selection-background-color: {GREEN_SOFT};
                              selection-color: {INK}; outline: 0; padding: 4px; }}
QCheckBox {{ font-size: 10.5pt; spacing: 8px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid {BORDER_STRONG}; background: {CARD}; }}
QCheckBox::indicator:checked {{ background: {GREEN}; border-color: {GREEN}; }}
{check_rule}
QRadioButton {{ font-size: 10.5pt; spacing: 8px; }}
QRadioButton::indicator {{ width: 16px; height: 16px; border-radius: 9px; border: 1px solid {BORDER_STRONG}; background: {CARD}; }}
QRadioButton::indicator:checked {{ width: 8px; height: 8px; border: 5px solid {GREEN}; background: {ON_GREEN}; }}
QSlider {{ min-height: 22px; background: transparent; }}
QSlider::groove:horizontal {{ height: 4px; background: {SUNKEN}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {GREEN}; border-radius: 2px; }}
QSlider::add-page:horizontal {{ background: {SUNKEN}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {KNOB}; border: 1px solid {BORDER_STRONG}; width: 16px; height: 16px;
                             margin: -7px 0; border-radius: 9px; }}
QSlider::handle:horizontal:hover {{ border: 2px solid {GREEN}; width: 14px; height: 14px; }}
QLabel#statValue {{ font-family: "{d}", "Segoe UI"; font-size: 20pt; font-weight: 600; }}
QSpinBox {{ background: {CARD}; border: 1px solid {BORDER_STRONG}; border-radius: 8px; padding: 6px 8px; font-size: 10.5pt; }}
QSpinBox:focus {{ border: 2px solid {GREEN}; padding: 5px 7px; }}
QLabel#valueChip {{ background: {RAISED}; border: 1px solid {BORDER}; border-radius: 6px; padding: 3px 0;
                   font-size: 9.5pt; font-weight: 600; color: {INK}; }}
QProgressBar {{ background: {SUNKEN}; border: none; border-radius: 4px; height: 8px; max-height: 8px; }}
QProgressBar::chunk {{ background: {GREEN}; border-radius: 4px; }}
QListWidget {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 12px; padding: 4px; outline: 0; }}
QListWidget::item {{ border-radius: 8px; padding: 8px; margin: 1px 0; }}
QListWidget::item:selected {{ background: {GREEN_SOFT}; color: {INK}; }}
QListWidget::item:hover {{ background: {HOVER}; }}
QMenu {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 7px 18px; border-radius: 6px; font-size: 10pt; }}
QMenu::item:selected {{ background: {GREEN_SOFT}; color: {INK}; }}
QMenu::item:disabled {{ color: {DISABLED}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px 3px; }}
QScrollBar:vertical:hover {{ margin: 2px 1px; }}
QScrollBar::handle:vertical {{ background: {SCROLL}; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {SCROLL_HOVER}; border-radius: 4px; }}
QScrollBar::handle:vertical:pressed {{ background: {FAINT}; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QToolButton#toTop {{ background: {CARD}; border: 1px solid {BORDER_STRONG}; border-radius: 19px; }}
QToolButton#toTop:hover {{ background: {GREEN_SOFT}; border-color: {GREEN}; }}
QFrame#stickyHost {{ background: {BG}; border: none; border-bottom: 1px solid {BORDER}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QToolTip {{ background: {TIP_BG}; color: {TIP_INK}; border: none; padding: 6px 8px; border-radius: 6px; }}
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
    "star": '<path d="M11.5 2.6a.6.6 0 0 1 1 0l2.6 5.3a1.5 1.5 0 0 0 1.1.8l5.8.8a.6.6 0 0 1 .3 1l-4.2 4.1'
    'a1.5 1.5 0 0 0-.4 1.3l1 5.8a.6.6 0 0 1-.8.6l-5.2-2.7a1.5 1.5 0 0 0-1.4 0l-5.2 2.7a.6.6 0 0 1-.8-.6l1-5.8'
    'a1.5 1.5 0 0 0-.4-1.3L1.7 10.5a.6.6 0 0 1 .3-1l5.8-.8a1.5 1.5 0 0 0 1.1-.8z"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "lightbulb": '<path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5"/>'
    '<path d="M9 18h6"/><path d="M10 22h4"/>',
    "chevron-up": '<path d="m18 15-6-6-6 6"/>',
    "study": '<path d="M21.4 10.9a1 1 0 0 0 0-1.8L12.8 5.2a2 2 0 0 0-1.7 0L2.6 9.1a1 1 0 0 0 0 1.8l8.6 3.9a2 2 0 0 0 1.7 0z"/>'
    '<path d="M22 10v6"/><path d="M6 12.5V16a6 3 0 0 0 12 0v-3.5"/>',
}


def icon_svg(name: str, color: str | None = None, size: int = 24, stroke: float = 2.0, fill: str = "none") -> str:
    body = _ICONS[name]
    color = color or INK
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24" fill="{fill}" '
        f'stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    )


_ICON_CACHE: dict[tuple[str, str, int, str], QIcon] = {}


def icon(name: str, color: str | None = None, size: int = 18, fill: str = "none") -> QIcon:
    """Иконка из SVG. Одинаковые не рисуем заново: в длинных списках их сотни."""
    color = color or INK
    key = (name, str(color), size, str(fill))
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return cached
    out = _render_icon(name, color, size, fill)
    _ICON_CACHE[key] = out
    return out


def toggle_icon(name: str, off: str, on: str, size: int = 18) -> QIcon:
    """Иконка кнопки-переключателя: тихая у невыбранной и цветом акцента у выбранной."""
    out = QIcon()
    for color, state in ((off, QIcon.State.Off), (on, QIcon.State.On)):
        src = icon(name, color, size)
        for scale in (1, 2):
            pm = src.pixmap(QSize(size, size), float(scale))
            for mode in (QIcon.Mode.Normal, QIcon.Mode.Active, QIcon.Mode.Selected):
                out.addPixmap(pm, mode, state)
    return out


def _render_icon(name: str, color: str, size: int, fill: str) -> QIcon:
    from PySide6.QtSvg import QSvgRenderer

    out = QIcon()
    for scale in (1, 2):
        px = size * scale
        pm = QPixmap(px, px)
        pm.fill(Qt.GlobalColor.transparent)
        renderer = QSvgRenderer(QByteArray(icon_svg(name, color, fill=fill).encode()))
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
