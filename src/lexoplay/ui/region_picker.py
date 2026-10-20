"""Выделение окна диалога мышкой (экран 2 на канвасе).

Рисуем поверх стоп-кадра игры: сначала снимок, потом окно выделения на его месте.
Выделению нужен фокус (Enter, Esc, Tab), а полноэкранные игры, потеряв фокус,
сворачиваются. Со стоп-кадром это неважно: игрок видит ту же картинку, а после Enter
LexoPlay разворачивает игру обратно. Результат в долях 0..1 от игры.

Здесь же плашку перевода можно перетащить в своё место и потянуть за правый край,
чтобы сделать шире или уже. R возвращает её на место по умолчанию.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from lexoplay.geometry import Rect, denormalize, font_px_for, normalize, place_strip
from lexoplay.ui.theme import ACCENT, FONT_FAMILY, NAME, STRIP_BG

Placer = Callable[[QWidget, Rect], None]

MIN_SIZE = 0.02  # меньше 2% экрана считаем случайным кликом


class RegionPicker(QWidget):
    accepted = Signal(object, object, object)  # окно диалога, имя или None, своё место перевода или None
    cancelled = Signal()

    def __init__(
        self,
        display: Rect,
        game_title: str,
        region: Rect | None = None,
        name_region: Rect | None = None,
        placer: Placer | None = None,
        on_native: Callable[[int], None] | None = None,
        background: QImage | None = None,
        strip: Rect | None = None,
        strip_place: str = "above",
        font_scale: float = 1.0,
    ) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._display = display
        self._title = game_title
        self._region = region
        self._name = name_region
        self._mode = "dialog"  # dialog | name
        self._drag_from: QPointF | None = None
        self._drag_to: QPointF | None = None
        self._placer = placer
        self._done = False
        self._background = background
        self._strip = strip  # своё место плашки перевода (доли окна) или None = по настройке
        self._strip_place = strip_place
        self._font_scale = font_scale
        self._strip_drag: str | None = None  # move | resize
        self._strip_off = QPointF()
        self._strip_press = QPointF()
        self._strip_before: Rect | None = strip
        self.winId()
        if on_native:
            on_native(int(self.winId()))

    def open(self) -> None:
        if self._placer:
            self._placer(self, self._display)
        else:
            x, y, w, h = self._display.to_int()
            self.setGeometry(x, y, w, h)
        self.show()
        if self._placer:
            self._placer(self, self._display)
        self.raise_()
        self.activateWindow()
        self.setFocus()

    # --- ввод ---
    def _bounds(self) -> Rect:
        return Rect(0, 0, float(self.width()), float(self.height()))

    def _strip_h(self) -> float:
        return font_px_for(float(self.height()), 19.0, self._font_scale) * 3.0

    def strip_rect(self) -> QRectF | None:
        """Где будет плашка перевода (в пикселях окна выделения). Пока выделяют имя, её нет."""
        if self._region is None or self._mode != "dialog":
            return None
        bounds = self._bounds()
        h = self._strip_h()
        if self._strip is not None:
            r = denormalize(self._strip, bounds)
            return QRectF(r.x, r.y, max(200.0, r.w), h)
        region = denormalize(self._region, bounds)
        gap = 8.0
        if self._strip_place == "top":
            r = Rect(region.x, max(8.0, bounds.h * 0.03), region.w, h)
        elif self._strip_place == "below" and region.bottom + gap + h <= bounds.h:
            r = Rect(region.x, region.bottom + gap, region.w, h)
        else:
            r = place_strip(region, h, bounds, gap=gap)
        return QRectF(r.x, r.y, r.w, r.h)

    def _set_strip(self, rect: QRectF) -> None:
        bounds = self._bounds()
        w = max(200.0, min(rect.width(), bounds.w - 8))
        x = max(0.0, min(rect.x(), bounds.w - w))
        y = max(0.0, min(rect.y(), bounds.h - rect.height()))
        self._strip = normalize(Rect(x, y, w, rect.height()), bounds)

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        strip = self.strip_rect()
        if e.button() == Qt.MouseButton.LeftButton and strip is not None and strip.contains(e.position()):
            self._strip_drag = "resize" if e.position().x() >= strip.right() - 14 else "move"
            self._strip_off = e.position() - strip.topLeft()
            self._strip_before = self._strip
            self._strip_press = e.position()
            self._set_strip(strip)
            self.update()
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_from = e.position()
            self._drag_to = e.position()
            self.update()
        elif e.button() == Qt.MouseButton.RightButton:
            self._cancel()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        strip = self.strip_rect()
        if self._strip_drag and strip is not None:
            if self._strip_drag == "move":
                strip.moveTopLeft(e.position() - self._strip_off)
            else:
                strip.setRight(e.position().x())
            self._set_strip(strip)
            self.update()
            return
        if strip is not None and strip.contains(e.position()):
            near_edge = e.position().x() >= strip.right() - 14
            self.setCursor(Qt.CursorShape.SizeHorCursor if near_edge else Qt.CursorShape.SizeAllCursor)
        else:
            self.setCursor(Qt.CursorShape.CrossCursor)
        if self._drag_from is not None:
            self._drag_to = e.position()
            self.update()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if self._strip_drag:
            self._strip_drag = None
            moved = e.position() - self._strip_press
            if abs(moved.x()) + abs(moved.y()) < 3:
                self._strip = self._strip_before  # просто клик: место не меняем
            self.update()
            return
        if e.button() != Qt.MouseButton.LeftButton or self._drag_from is None:
            return
        a, b = self._drag_from, e.position()
        self._drag_from = self._drag_to = None
        r = normalize(Rect.from_points(a.x(), a.y(), b.x(), b.y()), self._bounds())
        if r.w >= MIN_SIZE and r.h >= MIN_SIZE:
            if self._mode == "dialog":
                self._region = r
            else:
                self._name = r
        self.update()

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        key = e.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._region is not None:
                self._done = True
                self.accepted.emit(self._region, self._name, self._strip)
                self.close()
        elif key == Qt.Key.Key_Escape:
            self._cancel()
        elif key == Qt.Key.Key_Tab:
            self._mode = "name" if self._mode == "dialog" else "dialog"
            self.update()
        elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self._mode == "name":
            self._name = None
            self.update()
        elif key == Qt.Key.Key_R:
            self._strip = None  # плашка перевода снова на месте по умолчанию
            self.update()

    def focusNextPrevChild(self, _next: bool) -> bool:  # noqa: N802
        return False  # иначе Qt съедает Tab на переключение фокуса

    def _cancel(self) -> None:
        if not self._done:
            self._done = True
            self.cancelled.emit()
        self.close()

    def closeEvent(self, e) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        if not self._done:
            self._done = True
            self.cancelled.emit()
        super().closeEvent(e)

    # --- отрисовка ---
    def paintEvent(self, _e: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._background is not None and not self._background.isNull():
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawImage(QRectF(self.rect()), self._background)
            p.fillRect(self.rect(), QColor(0, 0, 0, 90))
        else:
            p.fillRect(self.rect(), QColor(0, 0, 0, 120))
        bounds = self._bounds()
        for rect, color, label in ((self._region, ACCENT, "диалог"), (self._name, NAME, "имя")):
            if rect is not None:
                self._paint_rect(p, denormalize(rect, bounds), color, label)
        if self._drag_from is not None and self._drag_to is not None:
            color = ACCENT if self._mode == "dialog" else NAME
            r = Rect.from_points(self._drag_from.x(), self._drag_from.y(), self._drag_to.x(), self._drag_to.y())
            self._paint_rect(p, r, color, None)
        self._paint_strip(p)
        self._paint_panel(p)
        p.end()

    def _paint_strip(self, p: QPainter) -> None:
        strip = self.strip_rect()
        if strip is None:
            return
        bg = QColor(STRIP_BG)
        bg.setAlphaF(0.88)
        p.setPen(QPen(QColor(255, 255, 255, 200 if self._strip_drag else 120), 1.5, Qt.PenStyle.DashLine))
        p.setBrush(bg)
        p.drawRoundedRect(strip, 10, 10)
        px = font_px_for(float(self.height()), 19.0, self._font_scale)
        f = QFont(FONT_FAMILY)
        f.setPixelSize(round(px))
        p.setFont(f)
        p.setPen(QColor("#FFFFFF"))
        text = "Здесь будет перевод. Перетащи плашку, куда удобно, или потяни за правый край"
        p.drawText(strip.adjusted(px * 0.9, px * 0.5, -px * 1.4, -px * 0.3), int(Qt.TextFlag.TextWordWrap), text)
        # уголок «потяни» справа
        p.setPen(QPen(QColor(255, 255, 255, 160), 2))
        x, y = strip.right() - 7, strip.center().y()
        p.drawLine(QPointF(x, y - 7), QPointF(x, y + 7))
        p.drawLine(QPointF(x - 4, y - 4), QPointF(x - 4, y + 4))
        tag = QRectF(strip.x(), strip.y() - 22, 84, 20)
        if tag.y() < 0:
            tag.moveTop(strip.bottom() + 2)
        tf = QFont(FONT_FAMILY)
        tf.setPixelSize(13)
        tf.setBold(True)
        p.setFont(tf)
        p.fillRect(tag, QColor("#E8E6DF"))
        p.setPen(QColor("#1F1F1D"))
        p.drawText(tag, Qt.AlignmentFlag.AlignCenter, "перевод")

    def _paint_rect(self, p: QPainter, r: Rect, color: QColor, label: str | None) -> None:
        box = QRectF(r.x, r.y, r.w, r.h)
        fill = QColor(color)
        fill.setAlpha(28)
        p.fillRect(box, fill)
        pen = QPen(color, 2, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(box)
        if label:
            f = QFont(FONT_FAMILY)
            f.setPixelSize(13)
            f.setBold(True)
            p.setFont(f)
            tag = QRectF(box.x(), box.y() - 22, 70, 20)
            if tag.y() < 0:
                tag.moveTop(box.y() + 4)
            p.fillRect(tag, color)
            p.setPen(QColor("#06281F"))
            p.drawText(tag, Qt.AlignmentFlag.AlignCenter, label)

    def _paint_panel(self, p: QPainter) -> None:
        w = min(self.width() - 32, 620)
        title = QFont(FONT_FAMILY)
        title.setPixelSize(17)
        title.setBold(True)
        body = QFont(FONT_FAMILY)
        body.setPixelSize(13)
        if self._mode == "dialog":
            head = "Обведи мышкой окно диалога" if self._region is None else "Готово? Нажми Enter"
        else:
            head = "Обведи имя говорящего (необязательно)"
        text = (
            f"{self._title}: рамка запомнится, дальше хватит одной клавиши перевода. "
            "Плашку перевода можно перетащить в своё место.\n"
            "Enter сохранить · Esc или правый клик отмена · Tab рамка для имени"
            + (" · Delete убрать имя" if self._mode == "name" else "")
            + (" · R плашку на место" if self._strip is not None else "")
        )
        flags = int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft)
        inner = w - 40
        body_h = QFontMetricsF(body).boundingRect(QRectF(0, 0, inner, 1000), flags, text).height()
        box = QRectF((self.width() - w) / 2, 24, w, 14 + 26 + 8 + body_h + 16)
        p.setPen(QColor("#3A3D45"))
        p.setBrush(QColor(18, 21, 27, 240))
        p.drawRoundedRect(box, 12, 12)
        p.setFont(title)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(QRectF(box.x() + 20, box.y() + 14, inner, 26), int(Qt.AlignmentFlag.AlignLeft), head)
        p.setFont(body)
        p.setPen(QColor("#B4B2A9"))
        p.drawText(QRectF(box.x() + 20, box.y() + 48, inner, body_h + 2), flags, text)
