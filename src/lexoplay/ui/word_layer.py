"""Режим слов (F3): подчёркивания под словами реплики и карточка слова.

Отдельное окно поверх игры размером с display_rect. В отличие от строки перевода, оно
принимает клики, но только там, где что-то нарисовано: прозрачные пиксели слоистого окна
Windows пропускает в игру. Поэтому область диалога заливаем почти невидимым цветом
(альфа 1/255), а всё остальное остаётся игрой. Клик мимо области уходит в игру, наше
окно теряет фокус, режим закрывается сам.

Клавиши: Esc выход, Enter добавить слово. Shift+клик выбирает фразу до 5 слов.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from lexoplay.geometry import Rect, denormalize, font_px_for
from lexoplay.ui.theme import ACCENT, FONT_FAMILY, STRIP_BG, TEXT, TEXT_MUTED
from lexoplay.wordmode import CardState, WordView, place_card, selection_range, union

Placer = Callable[[QWidget, Rect], None]

KNOWN = QColor("#F2C94C")
IDLE_MS = 15_000


def _qt_placer(widget: QWidget, rect: Rect) -> None:
    x, y, w, h = rect.to_int()
    widget.setGeometry(x, y, w, h)


class WordLayer(QWidget):
    selected = Signal(list)  # индексы выбранных слов
    add_clicked = Signal()
    finished = Signal(str)  # причина: esc, idle, focus, hotkey, closed

    def __init__(self, placer: Placer | None = None, on_native: Callable[[int], None] | None = None) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._placer = placer or _qt_placer
        self._display = Rect(0, 0, 1280, 720)
        self._region = Rect(0.05, 0.75, 0.9, 0.2)
        self._views: list[WordView] = []
        self._sel: list[int] = []
        self._anchor: int | None = None
        self._hover: int | None = None
        self._card: CardState | None = None
        self._button_rect: QRectF | None = None
        self._card_rect: QRectF | None = None
        self._strip: Rect | None = None  # строка перевода: карточка её не закрывает
        self._open = False
        self._guard = False  # первые мгновения после открытия: фокус ещё «дребезжит»
        self.font_scale = 1.0
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.setInterval(IDLE_MS)
        self._idle.timeout.connect(lambda: self.finish("idle"))
        self.winId()
        if on_native:
            on_native(int(self.winId()))

    # ------------------------------------------------------------------ состояние
    @property
    def is_open(self) -> bool:
        return self._open

    @property
    def selection(self) -> list[int]:
        return list(self._sel)

    def open(self, display: Rect, region: Rect, views: list[WordView], strip: Rect | None = None) -> None:
        self._display, self._region, self._views, self._strip = display, region, views, strip
        self._sel, self._anchor, self._hover, self._card = [], None, None, None
        self._open = True
        self._guard = True
        QTimer.singleShot(450, self._end_guard)
        self._placer(self, display)
        self.show()
        self._placer(self, display)
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self._idle.start()
        self.update()

    def _end_guard(self) -> None:
        self._guard = False

    def set_display_rect(self, rect: Rect) -> None:
        self._display = rect
        if self.isVisible():
            self._placer(self, rect)

    def set_views(self, views: list[WordView]) -> None:
        self._views = views
        self.update()

    def set_card(self, card: CardState | None) -> None:
        self._card = card
        self.update()

    def select(self, indices: list[int]) -> None:
        """Выбор программно (тесты, самопроверка)."""
        self._sel = sorted(indices)
        self._anchor = self._sel[0] if self._sel else None
        self.update()

    def finish(self, reason: str) -> None:
        if not self._open:
            return
        self._open = False
        self._idle.stop()
        self.hide()
        self.finished.emit(reason)

    # ------------------------------------------------------------------ геометрия
    def _bounds(self) -> Rect:
        return Rect(0, 0, float(self.width()), float(self.height()))

    def _px(self) -> float:
        return font_px_for(float(self.height()), 17.0, self.font_scale)

    def _word_rect(self, v: WordView) -> QRectF:
        r = denormalize(v.rect, self._bounds())
        return QRectF(r.x, r.y, r.w, r.h)

    def _hit_word(self, pos: QPointF) -> int | None:
        for v in self._views:
            if v.clickable and self._word_rect(v).adjusted(-3, -3, 3, 3).contains(pos):
                return v.index
        return None

    # ------------------------------------------------------------------ события
    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        self._idle.start()
        hover = self._hit_word(e.position())
        over_button = bool(self._button_rect and self._button_rect.contains(e.position()))
        self.setCursor(Qt.CursorShape.PointingHandCursor if hover is not None or over_button else Qt.CursorShape.ArrowCursor)
        if hover != self._hover:
            self._hover = hover
            self.update()

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        self._idle.start()
        if e.button() != Qt.MouseButton.LeftButton:
            return
        pos = e.position()
        if self._button_rect and self._button_rect.contains(pos):
            if self._card and self._card.button_enabled:
                self.add_clicked.emit()
            return
        if self._card_rect and self._card_rect.contains(pos):
            return
        idx = self._hit_word(pos)
        if idx is None:
            return
        shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        sel = selection_range(self._anchor, idx) if shift else [idx]
        if not shift:
            self._anchor = idx
        self._sel = sel
        self.update()
        self.selected.emit(sel)

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        self._idle.start()
        if e.key() == Qt.Key.Key_Escape:
            self.finish("esc")
        elif e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._card and self._card.button_enabled:
                self.add_clicked.emit()
        else:
            super().keyPressEvent(e)

    def changeEvent(self, e) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        super().changeEvent(e)
        if e.type() == e.Type.ActivationChange and self._open and not self._guard and not self.isActiveWindow():
            self.finish("focus")

    # ------------------------------------------------------------------ отрисовка
    def paintEvent(self, _e: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        bounds = self._bounds()
        region = denormalize(self._region, bounds)
        catch = QRectF(region.x, region.y, region.w, region.h).adjusted(-10, -10, 10, 10)
        p.fillRect(catch, QColor(0, 0, 0, 1))  # ловим клики только над областью диалога

        px = self._px()
        line_w = max(1.5, px / 11)
        for v in self._views:
            r = self._word_rect(v)
            if v.index in self._sel:
                fill = QColor(ACCENT)
                fill.setAlphaF(0.34)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(fill)
                p.drawRoundedRect(r.adjusted(-3, -2, 3, 2), 4, 4)
            elif v.index == self._hover and v.clickable:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 34))
                p.drawRoundedRect(r.adjusted(-3, -2, 3, 2), 4, 4)
            if not v.clickable or v.stop:
                continue
            y = r.bottom() + line_w * 1.6
            if v.known:
                pen = QPen(KNOWN, line_w * 1.2)
            else:
                c = QColor(ACCENT)
                c.setAlphaF(0.85)
                pen = QPen(c, line_w)
                pen.setStyle(Qt.PenStyle.DotLine)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))

        self._button_rect = None
        self._card_rect = None
        if self._card and self._sel:
            sel = union([denormalize(self._views[i].rect, bounds) for i in self._sel if i < len(self._views)])
            self._paint_card(p, self._card, sel, bounds, px)
        p.end()

    def _paint_card(self, p: QPainter, card: CardState, sel: Rect, bounds: Rect, px: float) -> None:
        pad = px * 0.95
        card_w = min(max(px * 20, 280.0), bounds.w - 16)
        inner = card_w - 2 * pad

        def font(size: float, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
            f = QFont(FONT_FAMILY)
            f.setPixelSize(max(1, round(size)))
            f.setWeight(weight)
            return f

        f_title = font(px * 1.4, QFont.Weight.DemiBold)
        f_sub = font(px * 0.8)
        f_main = font(px)
        f_more = font(px * 0.9)
        f_note = font(px * 0.78)
        f_btn = font(px * 0.9, QFont.Weight.DemiBold)
        flags = int(Qt.TextFlag.TextWordWrap)

        def height(f: QFont, text: str) -> float:
            return QFontMetricsF(f).boundingRect(QRectF(0, 0, inner, 10000), flags, text).height()

        # раскладка сверху вниз: (шрифт, текст, цвет) или ("skeleton", ширина) или ("button")
        blocks: list[tuple] = [("text", f_title, card.title, TEXT)]
        if card.subtitle:
            blocks.append(("text", f_sub, card.subtitle, TEXT_MUTED))
        if card.phase == "loading":
            blocks += [("skeleton", 0.7), ("skeleton", 0.45)]
        for i, s in enumerate(card.senses[:3]):
            blocks.append(("text", f_main if i == 0 else f_more, s, TEXT if i == 0 else TEXT_MUTED))
        if card.note:
            blocks.append(("text", f_note, card.note, ACCENT if card.phase == "saved" else TEXT_MUTED))
        if card.button:
            blocks.append(("button",))
        blocks.append(("text", f_note, "Shift+клик: фраза · Esc: выйти", TEXT_MUTED))

        gap = px * 0.35
        heights: list[float] = []
        btn_h = px * 2.0
        for b in blocks:
            if b[0] == "text":
                heights.append(height(b[1], b[2]))
            elif b[0] == "skeleton":
                heights.append(px * 0.75)
            else:
                heights.append(btn_h + px * 0.25)
        card_h = sum(heights) + gap * (len(blocks) - 1) + 2 * pad
        avoid = union([denormalize(self._region, bounds)] + ([self._strip] if self._strip else []))
        box = place_card(sel, card_w, card_h, bounds, gap=max(8.0, px * 0.6), avoid=avoid)
        rect = QRectF(box.x, box.y, box.w, box.h)
        self._card_rect = rect

        bg = QColor(STRIP_BG)
        bg.setAlphaF(0.95)
        p.setPen(QPen(QColor(255, 255, 255, 28), 1))
        p.setBrush(bg)
        p.drawRoundedRect(rect, 12, 12)

        y = rect.y() + pad
        x = rect.x() + pad
        for b, h in zip(blocks, heights, strict=True):
            if b[0] == "text":
                p.setFont(b[1])
                p.setPen(b[3])
                p.drawText(QRectF(x, y, inner, h + 1), flags, b[2])
            elif b[0] == "skeleton":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 30))
                p.drawRoundedRect(QRectF(x, y + h * 0.15, inner * b[1], h * 0.7), h * 0.3, h * 0.3)
            else:
                label = card.button or ""
                fm = QFontMetricsF(f_btn)
                bw = fm.horizontalAdvance(label) + px * 2.2
                btn = QRectF(x, y + px * 0.25, bw, btn_h)
                self._button_rect = btn
                fill = QColor(ACCENT)
                if not card.button_enabled:
                    fill.setAlphaF(0.35)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(fill)
                p.drawRoundedRect(btn, btn_h / 2, btn_h / 2)
                p.setFont(f_btn)
                p.setPen(QColor("#06261F"))
                p.drawText(btn, int(Qt.AlignmentFlag.AlignCenter), label)
                p.setFont(f_note)
                p.setPen(TEXT_MUTED)
                p.drawText(
                    QRectF(btn.right() + px * 0.6, btn.y(), inner - bw - px * 0.6, btn_h),
                    int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                    "Enter",
                )
            y += h + gap
