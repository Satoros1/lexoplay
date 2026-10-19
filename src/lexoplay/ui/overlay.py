"""Оверлей поверх игры: строка перевода над окном диалога и тосты в углу.

Одно прозрачное окно размером с display_rect, клики проходят насквозь, фокус не берёт.
Ставится в физических координатах через `placer` (на Windows это SetWindowPos), а рисует
всё в логических пикселях относительно своего размера, поэтому DPI и Magpie не мешают.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPaintEvent
from PySide6.QtWidgets import QWidget

from lexoplay.geometry import Rect, denormalize, font_px_for, place_strip
from lexoplay.ui.theme import ACCENT, FONT_FAMILY, STRIP_BG, TEXT, TEXT_MUTED

Placer = Callable[[QWidget, Rect], None]


def _qt_placer(widget: QWidget, rect: Rect) -> None:
    x, y, w, h = rect.to_int()
    widget.setGeometry(x, y, w, h)


class Overlay(QWidget):
    def __init__(self, placer: Placer | None = None, on_native: Callable[[int], None] | None = None) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self._placer = placer or _qt_placer
        self._display = Rect(0, 0, 1280, 720)
        self._region: Rect | None = None
        self._text: str | None = None
        self._busy = False
        self._toast: str | None = None
        self.font_scale = 1.0
        self.opacity = 0.84
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._clear_toast)
        self.winId()  # создать нативное окно заранее
        if on_native:
            on_native(int(self.winId()))

    # --- состояние ---
    @property
    def strip_visible(self) -> bool:
        return self._text is not None or self._busy

    def set_display_rect(self, rect: Rect) -> None:
        self._display = rect
        if self.isVisible():
            self._placer(self, rect)

    def show_busy(self, region: Rect) -> None:
        self._region, self._text, self._busy = region, None, True
        self._refresh()

    def show_text(self, region: Rect, text: str) -> None:
        self._region, self._text, self._busy = region, text, False
        self._refresh()

    def hide_strip(self) -> None:
        self._text, self._busy = None, False
        self._refresh()

    def strip_rect(self) -> Rect | None:
        """Где сейчас строка перевода, в координатах окна (оно совпадает с display_rect)."""
        if not self.strip_visible:
            return None
        box, _font, _text = self._strip_geometry(self._text if self._text is not None else "…")
        return Rect(box.x(), box.y(), box.width(), box.height())

    def toast(self, text: str, ms: int = 2400) -> None:
        self._toast = text
        self._toast_timer.start(ms)
        self._refresh()

    def _clear_toast(self) -> None:
        self._toast = None
        self._refresh()

    def _refresh(self) -> None:
        need = self.strip_visible or self._toast is not None
        if need:
            if not self.isVisible():
                self._placer(self, self._display)
                self.show()
                self._placer(self, self._display)
            self.update()
        elif self.isVisible():
            self.hide()

    # --- отрисовка ---
    def _strip_geometry(self, text: str) -> tuple[QRectF, QFont, QRectF]:
        w, h = float(self.width()), float(self.height())
        bounds = Rect(0, 0, w, h)
        region = denormalize(self._region or Rect(0.05, 0.75, 0.9, 0.2), bounds)
        if region.w < 280:  # узкая область: строку всё равно делаем читаемой ширины
            cx = region.x + region.w / 2
            nw = min(w - 16, 420.0)
            region = Rect(max(8.0, min(cx - nw / 2, w - 8 - nw)), region.y, nw, region.h)
        px = font_px_for(h, 19.0, self.font_scale)
        font = QFont(FONT_FAMILY)
        font.setWeight(QFont.Weight.Medium)

        def measure(size: float) -> tuple[QRectF, QFontMetricsF]:
            font.setPixelSize(max(1, round(size)))
            fm = QFontMetricsF(font)
            pad_x = size * 0.9
            text_w = max(40.0, region.w - 2 * pad_x)
            br = fm.boundingRect(QRectF(0, 0, text_w, 100000), Qt.TextFlag.TextWordWrap, text)
            return br, fm

        br, fm = measure(px)
        if br.height() > fm.lineSpacing() * 3.2:  # больше трёх строк: мельче, но не меньше 14
            px = max(14.0, px * 0.8)
            br, fm = measure(px)
        pad_x, pad_y = px * 0.9, px * 0.6
        strip_h = br.height() + 2 * pad_y
        strip = place_strip(region, strip_h, bounds, gap=max(6.0, px * 0.4))
        box = QRectF(strip.x, strip.y, strip.w, strip.h)
        text_rect = QRectF(strip.x + pad_x, strip.y + pad_y, strip.w - 2 * pad_x, br.height())
        return box, font, text_rect

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        if self.strip_visible:
            text = self._text if self._text is not None else "…"
            box, font, text_rect = self._strip_geometry(text)
            bg = QColor(STRIP_BG)
            bg.setAlphaF(max(0.3, min(1.0, self.opacity)))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            radius = min(12.0, box.height() / 2)
            p.drawRoundedRect(box, radius, radius)
            p.setFont(font)
            p.setPen(TEXT if self._text is not None else TEXT_MUTED)
            p.drawText(text_rect, int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft), text)
        if self._toast:
            self._paint_toast(p, self._toast)
        p.end()

    def _paint_toast(self, p: QPainter, text: str) -> None:
        h = float(self.height())
        size = max(12.0, 14.0 * h / 1080.0 * self.font_scale)
        font = QFont(FONT_FAMILY)
        font.setPixelSize(round(size))
        fm = QFontMetricsF(font)
        max_w = min(self.width() * 0.6, 520.0)
        br = fm.boundingRect(QRectF(0, 0, max_w, 10000), Qt.TextFlag.TextWordWrap, text)
        pad_x, pad_y, dot = size * 1.0, size * 0.55, size * 0.5
        w = br.width() + pad_x * 2 + dot * 1.6
        hh = br.height() + pad_y * 2
        margin = size * 1.4
        box = QRectF(self.width() - margin - w, margin, w, hh)
        bg = QColor(STRIP_BG)
        bg.setAlphaF(0.88)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(box, min(hh / 2, 16.0), min(hh / 2, 16.0))
        p.setBrush(ACCENT)
        p.drawEllipse(QRectF(box.x() + pad_x, box.y() + pad_y + fm.ascent() / 2 - dot / 4, dot, dot))
        p.setFont(font)
        p.setPen(QColor("#D3D1C7"))
        p.drawText(
            QRectF(box.x() + pad_x + dot * 1.6, box.y() + pad_y, br.width() + 1, br.height()),
            int(Qt.TextFlag.TextWordWrap),
            text,
        )
