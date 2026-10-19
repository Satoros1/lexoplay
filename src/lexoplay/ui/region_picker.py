"""Выделение окна диалога мышкой поверх игры (экран 2 на канвасе).

Окно накрывает display_rect, затемняет игру и даёт нарисовать рамку. Tab переключает
на необязательную рамку для имени говорящего. Результат в долях 0..1 от игры.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from lexoplay.geometry import Rect, denormalize, normalize
from lexoplay.ui.theme import ACCENT, FONT_FAMILY, NAME

Placer = Callable[[QWidget, Rect], None]

MIN_SIZE = 0.02  # меньше 2% экрана считаем случайным кликом


class RegionPicker(QWidget):
    accepted = Signal(object, object)  # Rect, Rect | None
    cancelled = Signal()

    def __init__(
        self,
        display: Rect,
        game_title: str,
        region: Rect | None = None,
        name_region: Rect | None = None,
        placer: Placer | None = None,
        on_native: Callable[[int], None] | None = None,
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

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_from = e.position()
            self._drag_to = e.position()
            self.update()
        elif e.button() == Qt.MouseButton.RightButton:
            self._cancel()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if self._drag_from is not None:
            self._drag_to = e.position()
            self.update()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802
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
                self.accepted.emit(self._region, self._name)
                self.close()
        elif key == Qt.Key.Key_Escape:
            self._cancel()
        elif key == Qt.Key.Key_Tab:
            self._mode = "name" if self._mode == "dialog" else "dialog"
            self.update()
        elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self._mode == "name":
            self._name = None
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
        p.fillRect(self.rect(), QColor(0, 0, 0, 120))
        bounds = self._bounds()
        for rect, color, label in ((self._region, ACCENT, "диалог"), (self._name, NAME, "имя")):
            if rect is not None:
                self._paint_rect(p, denormalize(rect, bounds), color, label)
        if self._drag_from is not None and self._drag_to is not None:
            color = ACCENT if self._mode == "dialog" else NAME
            r = Rect.from_points(self._drag_from.x(), self._drag_from.y(), self._drag_to.x(), self._drag_to.y())
            self._paint_rect(p, r, color, None)
        self._paint_panel(p)
        p.end()

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
        w = min(self.width() - 32, 600)
        box = QRectF((self.width() - w) / 2, 24, w, 112)
        p.setPen(QColor("#3A3D45"))
        p.setBrush(QColor(18, 21, 27, 240))
        p.drawRoundedRect(box, 12, 12)
        title = QFont(FONT_FAMILY)
        title.setPixelSize(17)
        title.setBold(True)
        body = QFont(FONT_FAMILY)
        body.setPixelSize(13)
        p.setFont(title)
        p.setPen(QColor("#FFFFFF"))
        head = "Выдели окно диалога мышкой" if self._mode == "dialog" else "Выдели имя говорящего (необязательно)"
        p.drawText(QRectF(box.x() + 20, box.y() + 14, box.width() - 40, 24), int(Qt.AlignmentFlag.AlignLeft), head)
        p.setFont(body)
        p.setPen(QColor("#B4B2A9"))
        lines = [
            f"Игра: {self._title}. Область запомнится, дальше хватит F2.",
            "Enter сохранить · Esc отмена · Tab рамка для имени"
            + (" · Delete убрать имя" if self._mode == "name" else ""),
        ]
        for i, line in enumerate(lines):
            p.drawText(QRectF(box.x() + 20, box.y() + 48 + i * 22, box.width() - 40, 20), int(Qt.AlignmentFlag.AlignLeft), line)
