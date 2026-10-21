"""Снимок игры на её месте, пока сама игра свёрнута на время режима слов (F3).

Окно непрозрачное и лежит под слоем слов. В оконном режиме оно накрывает окно игры целиком,
с рамкой и заголовком: свёрнутая игра выглядит просто поставленной на паузу, а не пропавшей.
После выхода из режима снимок держится, пока игра разворачивается (полноэкранной на это
нужна секунда-другая), чтобы между ними не мелькал рабочий стол.

Клик по снимку (по заголовку окна, мимо слоя слов) закрывает режим, как клик мимо панели.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QImage, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import QWidget

from lexoplay.geometry import Rect

Placer = Callable[[QWidget, Rect], None]


def _qt_placer(widget: QWidget, rect: Rect) -> None:
    x, y, w, h = rect.to_int()
    widget.setGeometry(x, y, w, h)


class FreezeFrame(QWidget):
    clicked = Signal()  # кнопку мыши отпустили над снимком

    def __init__(self, placer: Placer | None = None, on_native: Callable[[int], None] | None = None) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._placer = placer or _qt_placer
        self._image: QImage | None = None
        self.rect_shown: Rect | None = None
        self.winId()
        if on_native:
            on_native(int(self.winId()))

    @property
    def showing(self) -> bool:
        return self._image is not None and self.isVisible()

    def show_image(self, image: QImage, rect: Rect) -> None:
        self._image = image
        self.rect_shown = rect
        self._placer(self, rect)
        self.show()
        self._placer(self, rect)
        self.repaint()

    def move_to(self, rect: Rect) -> None:
        """Полноэкранная игра свернулась и вернула монитору его разрешение: снимок на весь монитор."""
        if self._image is not None:
            self.rect_shown = rect
            self._placer(self, rect)
            self.update()

    def clear(self) -> None:
        self.hide()
        self._image = None
        self.rect_shown = None

    def paintEvent(self, _e: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        if self._image is None:
            p.fillRect(self.rect(), Qt.GlobalColor.black)
        else:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawImage(QRectF(self.rect()), self._image)
        p.end()

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        e.accept()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        e.accept()
        self.clicked.emit()
