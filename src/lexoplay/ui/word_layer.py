"""Режим слов (F3) и разбор реплики (F7): подчёркивания под словами, карточка слова, разбор.

Отдельное окно поверх игры размером с display_rect. Пока режим открыт, оно лежит над
всей игрой (слегка затемняет её) и отвечает на все клики над ней: по слову, по кнопкам,
по пустому месту (это просто закрывает режим). Сами клики и колесо до игры не доходят:
их ещё до окон забирает хук (win/llhooks.py) и передаёт сюда в click(). Иначе игры,
которые читают мышь через Raw Input или DirectInput, пропускали бы реплику.

Фокус окно не берёт (WS_EX_NOACTIVATE): полноэкранные игры, потеряв фокус, сворачиваются.
Поэтому Esc, Enter и стрелки приходят не сюда, а через App (тот же хук), пока режим открыт. Стрелками слова выбираются и без мыши: это выручает, когда игра держит
курсор у себя (тогда App ещё и забирает фокус на время режима, см. CursorGuard).

Перед тем как спрятаться, окно рисует пустой кадр: иначе при следующем F3 Windows на
миг показывала бы последний кадр (например, старый разбор реплики).
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QWheelEvent,
)
from PySide6.QtWidgets import QWidget

from lexoplay.geometry import Rect, denormalize, font_px_for
from lexoplay.ui.theme import ACCENT, FONT_FAMILY, NAME, STRIP_BG, TEXT, TEXT_MUTED
from lexoplay.wordmode import (
    CardState,
    ExplainState,
    WordView,
    next_word,
    place_card,
    selection_range,
    union,
    word_in_line,
)

Placer = Callable[[QWidget, Rect], None]

KNOWN = QColor("#F2C94C")
IDLE_MS = 30_000
PICK_MS = 350  # стрелками: значение слова запрашиваем, когда выбор остановился


def _qt_placer(widget: QWidget, rect: Rect) -> None:
    x, y, w, h = rect.to_int()
    widget.setGeometry(x, y, w, h)


class WordLayer(QWidget):
    selected = Signal(list)  # индексы выбранных слов
    add_clicked = Signal()
    explain_clicked = Signal()  # кнопка «Разбор реплики»
    retranslate_clicked = Signal()  # кнопка «Перевести иначе»
    explain_save = Signal(int)  # «+» у пункта разбора
    finished = Signal(str)  # причина: esc, idle, focus, hotkey, closed, outside

    def __init__(self, placer: Placer | None = None, on_native: Callable[[int], None] | None = None) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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
        self._explain: ExplainState | None = None
        self._explain_open = False
        self._explain_btn: QRectF | None = None
        self._retranslate_btn: QRectF | None = None
        self.can_retranslate = False  # у реплики есть перевод: можно попросить другой
        self.retranslating = False
        self._explain_rect: QRectF | None = None
        self._explain_saves: list[QRectF] = []
        self.font_scale = 1.0
        self.deck_label = ""
        self.explain_key = "F7"
        self.cursor_locked = False  # игра держит курсор: подсказка про стрелки
        self._focus: int | None = None  # куда двигают стрелки
        self._blank = False
        self._pick = QTimer(self)
        self._pick.setSingleShot(True)
        self._pick.setInterval(PICK_MS)
        self._pick.timeout.connect(lambda: self.selected.emit(list(self._sel)) if self._open and self._sel else None)
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
        self._sel, self._anchor, self._hover, self._card, self._focus = [], None, None, None, None
        self._explain, self._explain_open = None, False
        self.cursor_locked = False
        self._pick.stop()
        self._open = True
        self._placer(self, display)
        self.show()
        self._placer(self, display)
        self._idle.start()
        self.repaint()  # сразу свежий кадр

    def press_enter(self) -> None:
        """Enter с клавиатуры (через глобальную клавишу App, фокуса у окна нет)."""
        self._idle.start()
        if self._card and self._card.button_enabled:
            self.add_clicked.emit()

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

    @property
    def explain_open(self) -> bool:
        return self._explain_open

    def set_explain(self, state: ExplainState | None, *, show: bool = True) -> None:
        """Разбор реплики: показать панель (карточка слова при этом прячется)."""
        self._explain = state
        if show and state is not None:
            self._explain_open = True
            self._sel, self._card = [], None
        self._idle.start()
        self.update()

    def hide_explain(self) -> None:
        self._explain_open = False
        self.update()

    def select(self, indices: list[int]) -> None:
        """Выбор программно (тесты, самопроверка)."""
        self._sel = sorted(indices)
        self._anchor = self._sel[0] if self._sel else None
        self._focus = self._sel[-1] if self._sel else None
        self.update()

    def navigate(self, key: str) -> None:
        """Стрелки: left/right по словам, up/down по строкам, shift_left/shift_right растягивают фразу."""
        if not self._open or not self._views:
            return
        self._idle.start()
        self._explain_open = False  # как клик по слову: разбор прячется, F7 вернёт
        focus = self._focus if self._focus is not None else (self._sel[-1] if self._sel else None)
        step = 1 if key.endswith(("right", "down")) else -1
        if key.startswith("shift_"):
            if focus is None:
                focus = next_word(self._views, None, 1)
                if focus is None:
                    return
                self._anchor = focus
                sel = [focus]
            else:
                if self._anchor is None:
                    self._anchor = focus
                target = next_word(self._views, focus, step)
                if target is None:
                    return
                sel = selection_range(self._anchor, target)
                focus = sel[-1] if target >= self._anchor else sel[0]
        else:
            target = (word_in_line if key in ("up", "down") else next_word)(self._views, focus, step)
            if target is None:
                return
            focus, self._anchor, sel = target, target, [target]
        self._focus = focus
        if sel != self._sel:
            self._sel = sel
            self._card = None  # карточка придёт, когда выбор остановится
            self._pick.start()
        self.update()

    def request_retranslate(self) -> None:
        if self._open and self.can_retranslate and not self.retranslating:
            self._idle.start()
            self.retranslate_clicked.emit()

    def save_explain_item(self, index: int) -> None:
        """Цифра 1–5 при открытом разборе: «+» у этого пункта."""
        st = self._explain
        if self._open and self._explain_open and st is not None and st.phase == "ready" and 0 <= index < len(st.rows):
            self._idle.start()
            self.explain_save.emit(index)

    def finish(self, reason: str) -> None:
        if not self._open:
            return
        self._open = False
        self._idle.stop()
        self._pick.stop()
        # пустой кадр перед скрытием: иначе при следующем показе мелькнёт этот (с разбором, карточкой)
        self._blank = True
        self.repaint()
        self.hide()
        self._blank = False
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
        e.accept()  # клик дальше в игру не идёт (если хук не стоит: см. click)
        if e.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self.click(e.position(), right=e.button() == Qt.MouseButton.RightButton, shift=shift)
        elif self._open:
            self._idle.start()

    def click(self, pos: QPointF, *, right: bool = False, shift: bool = False) -> None:
        """Клик по слою в точке pos (логические пиксели окна).

        Обычно приходит от хука: он забрал клик у игры, и до окна тот уже не дошёл."""
        if not self._open:
            return
        self._idle.start()
        if right:
            self.finish("outside")
            return
        if self._explain_btn and self._explain_btn.contains(pos):
            self.explain_clicked.emit()
            return
        if self._retranslate_btn and self._retranslate_btn.contains(pos):
            self.retranslate_clicked.emit()
            return
        if self._explain_open and self._explain_rect is not None:
            for i, r in enumerate(self._explain_saves):
                if r.contains(pos):
                    self.explain_save.emit(i)
                    return
            if self._explain_rect.contains(pos):
                return
        if self._button_rect and self._button_rect.contains(pos):
            if self._card and self._card.button_enabled:
                self.add_clicked.emit()
            return
        if self._card_rect and self._card_rect.contains(pos):
            return
        idx = self._hit_word(pos)
        if idx is None:
            self.finish("outside")  # пустое место: закрыть режим, в игру клик не попадёт
            return
        self._explain_open = False  # слово важнее: разбор спрячется, F7 вернёт
        self._pick.stop()
        sel = selection_range(self._anchor, idx) if shift else [idx]
        if not shift:
            self._anchor = idx
        self._focus = idx
        self._sel = sel
        self.update()
        self.selected.emit(sel)

    def wheelEvent(self, e: QWheelEvent) -> None:  # noqa: N802
        e.accept()  # колесо над игрой в новеллах листает текст: пока режим открыт, не пускаем

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        # обычно сюда не попадает (фокуса нет), но если окно всё же активировали
        self._idle.start()
        shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        arrows = {Qt.Key.Key_Left: "left", Qt.Key.Key_Right: "right", Qt.Key.Key_Up: "up", Qt.Key.Key_Down: "down"}
        if e.key() == Qt.Key.Key_Escape:
            self.finish("esc")
        elif e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.press_enter()
        elif e.key() in arrows:
            key = arrows[e.key()]
            self.navigate(f"shift_{key}" if shift and key in ("left", "right") else key)
        else:
            super().keyPressEvent(e)

    # ------------------------------------------------------------------ отрисовка
    def paintEvent(self, _e: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        if self._blank:
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            p.fillRect(self.rect(), Qt.GlobalColor.transparent)
            p.end()
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        bounds = self._bounds()
        region = denormalize(self._region, bounds)
        p.fillRect(self.rect(), QColor(0, 0, 0, 34))  # ловим все клики над игрой и чуть затемняем её

        px = self._px()
        line_w = max(1.5, px / 11)
        explained: set[int] = set()
        if self._explain_open and self._explain and self._explain.phase == "ready":
            for row in self._explain.rows:
                explained.update(row.indices)
            self._paint_phrase_lines(p, line_w)
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
            if v.index in explained:
                continue  # выражение из разбора подчёркнуто целиком (_paint_phrase_lines)
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
        self._explain_rect = None
        self._explain_saves = []
        self._explain_btn = None
        self._retranslate_btn = None
        if self._explain_open and self._explain is not None:
            self._paint_explain(p, self._explain, region, bounds, px)
        elif self._card and self._sel:
            sel = union([denormalize(self._views[i].rect, bounds) for i in self._sel if i < len(self._views)])
            self._paint_card(p, self._card, sel, bounds, px)
        if not self._explain_open:
            self._paint_explain_button(p, region, bounds, px)
        p.end()

    def _paint_phrase_lines(self, p: QPainter, line_w: float) -> None:
        """Выражения из разбора: одна сплошная линия под каждым целиком, а не чёрточки под словами."""
        assert self._explain is not None
        pen = QPen(NAME, line_w * 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        by_index = {v.index: v for v in self._views}
        for row in self._explain.rows:
            run: list[QRectF] = []
            for i in sorted(row.indices) + [None]:  # type: ignore[list-item]
                r = self._word_rect(by_index[i]) if i is not None and i in by_index else None
                same_line = r is not None and run and abs(r.center().y() - run[-1].center().y()) < r.height() * 0.6
                if run and not same_line:  # строка кончилась (или выражение): линия под набранным куском
                    y = max(x.bottom() for x in run) + line_w * 1.6
                    p.drawLine(QPointF(run[0].left() - 2, y), QPointF(run[-1].right() + 2, y))
                    run = []
                if r is not None:
                    run.append(r)

    def _paint_explain_button(self, p: QPainter, region: Rect, bounds: Rect, px: float) -> None:
        """Кнопки над строкой перевода справа: «Перевести иначе» и «Разбор реплики»."""
        f = QFont(FONT_FAMILY)
        f.setPixelSize(max(12, round(px * 0.82)))
        f.setWeight(QFont.Weight.DemiBold)
        fm = QFontMetricsF(f)
        h = px * 1.75
        explain = f"Разбор реплики · {self.explain_key}" if self.explain_key else "Разбор реплики"
        buttons = [("explain", explain)]
        if self.can_retranslate:
            buttons.insert(0, ("retranslate", "Перевожу заново…" if self.retranslating else "Перевести иначе · R"))
        widths = [fm.horizontalAdvance(text) + px * 1.6 for _key, text in buttons]
        total = sum(widths) + px * 0.5 * (len(buttons) - 1)
        top, bottom = region.y, region.bottom
        if self._strip is not None:  # над строкой перевода, чтобы ничего не закрывать
            top, bottom = min(top, self._strip.y), max(bottom, self._strip.bottom)
        y = top - h - px * 0.4
        if y < 4:
            y = bottom + px * 0.4
        if y + h > bounds.h - 4:
            y = 4.0
        x = max(4.0, min(region.right - total, bounds.w - total - 6))
        if not self._sel and self._card is None:
            self._paint_hint(p, region, x, y, h, px)
        self._explain_btn = None
        self._retranslate_btn = None
        for (key, text), w in zip(buttons, widths, strict=True):
            rect = QRectF(x, y, w, h)
            busy = key == "retranslate" and self.retranslating
            if key == "explain":
                self._explain_btn = rect
            elif not busy:
                self._retranslate_btn = rect
            bg = QColor(STRIP_BG)
            bg.setAlphaF(0.92)
            p.setPen(QPen(QColor(NAME) if key == "explain" else QColor(ACCENT), 1.2))
            p.setBrush(bg)
            p.drawRoundedRect(rect, h / 2, h / 2)
            p.setFont(f)
            p.setPen(QColor("#D9D4FF") if key == "explain" else QColor("#BDEBDD") if not busy else TEXT_MUTED)
            p.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), text)
            x += w + px * 0.5

    def _paint_hint(self, p: QPainter, region: Rect, right: float, y: float, h: float, px: float) -> None:
        """Подсказка слева от кнопок, пока ничего не выбрано: как выбирать слова без мыши."""
        f = QFont(FONT_FAMILY)
        f.setPixelSize(max(11, round(px * 0.76)))
        fm = QFontMetricsF(f)
        if self.cursor_locked:
            text = "Курсор держит игра: слова стрелками ←→, Shift+→ фраза, Enter сохранить, Esc выйти"
        else:
            text = "Клик или ←→ по слову · Shift фраза · Esc выйти"
        x = max(4.0, region.x)
        room = right - x - px * 0.6
        if room < px * 8:
            return
        text = fm.elidedText(text, Qt.TextElideMode.ElideRight, room - px * 1.4)
        w = fm.horizontalAdvance(text) + px * 1.4
        rect = QRectF(x, y, w, h)
        bg = QColor(STRIP_BG)
        bg.setAlphaF(0.88)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(rect, h / 2, h / 2)
        p.setFont(f)
        p.setPen(QColor("#F2C94C") if self.cursor_locked else TEXT_MUTED)
        p.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), text)

    def _paint_explain(self, p: QPainter, st: ExplainState, region: Rect, bounds: Rect, px: float) -> None:
        pad = px * 0.95
        card_w = min(max(px * 26, 340.0), bounds.w * 0.55, bounds.w - 16)
        inner = card_w - 2 * pad
        btn = px * 1.5  # кнопка «+» у пункта

        def font(size: float, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
            f = QFont(FONT_FAMILY)
            f.setPixelSize(max(1, round(size)))
            f.setWeight(weight)
            return f

        f_title = font(px * 1.1, QFont.Weight.DemiBold)
        f_phrase = font(px * 0.98, QFont.Weight.DemiBold)
        f_main = font(px * 0.92)
        f_note = font(px * 0.78)
        flags = int(Qt.TextFlag.TextWordWrap)

        def height(f: QFont, text: str, width: float = inner) -> float:
            return QFontMetricsF(f).boundingRect(QRectF(0, 0, width, 10000), flags, text).height()

        # (вид, шрифт, текст, цвет, номер пункта или -1). Сначала смысл реплики целиком,
        # потом «дословно», потом только настоящие трудные выражения.
        blocks: list[tuple] = [("text", f_title, "Разбор реплики", TEXT, -1)]
        if st.phase == "loading":
            blocks += [("skeleton", 0.85), ("skeleton", 0.6), ("skeleton", 0.75), ("skeleton", 0.5)]
        elif st.phase == "error":
            blocks.append(("text", f_main, st.note or "Не получилось разобрать реплику.", TEXT_MUTED, -1))
        else:
            if st.summary:
                blocks.append(("text", f_main, st.summary, TEXT, -1))
            if st.literal:
                blocks.append(("text", f_note, f"Дословно: {st.literal}", TEXT_MUTED, -1))
            if st.rows:
                blocks.append(("gap",))
            elif not st.summary:
                blocks.append(("text", f_main, "Здесь всё просто: трудных мест нет.", TEXT_MUTED, -1))
            for i, row in enumerate(st.rows):
                head = f"{row.phrase}  ·  {row.base}" if row.base else row.phrase
                blocks.append(("phrase", f_phrase, head, QColor("#D9D4FF"), i))
                blocks.append(("text", f_main, row.meaning, TEXT, -1))
                if row.note:
                    blocks.append(("text", f_note, row.note, TEXT_MUTED, -1))
                if i < len(st.rows) - 1:
                    blocks.append(("gap",))
        keys = f"«+» или 1–{len(st.rows)} сохранит в слова · " if st.phase == "ready" and st.rows else ""
        blocks.append(("text", f_note, f"{keys}←→ к словам · Esc закрыть", TEXT_MUTED, -1))

        gap = px * 0.3
        heights: list[float] = []
        for b in blocks:
            if b[0] == "text":
                heights.append(height(b[1], b[2]))
            elif b[0] == "phrase":
                heights.append(max(btn, height(b[1], b[2], inner - btn - px * 0.5)))
            elif b[0] == "skeleton":
                heights.append(px * 0.8)
            else:
                heights.append(px * 0.35)
        card_h = sum(heights) + gap * (len(blocks) - 1) + 2 * pad
        card_h = min(card_h, bounds.h - 16)
        avoid = union([region] + ([self._strip] if self._strip else []))
        box = place_card(region, card_w, card_h, bounds, gap=max(8.0, px * 0.6), avoid=avoid)
        rect = QRectF(box.x, box.y, box.w, box.h)
        self._explain_rect = rect
        bg = QColor(STRIP_BG)
        bg.setAlphaF(0.96)
        p.setPen(QPen(QColor(255, 255, 255, 28), 1))
        p.setBrush(bg)
        p.drawRoundedRect(rect, 12, 12)
        p.save()
        p.setClipRect(rect)
        x, y = rect.x() + pad, rect.y() + pad
        saves: list[QRectF] = []
        for b, h in zip(blocks, heights, strict=True):
            if b[0] == "text":
                p.setFont(b[1])
                p.setPen(b[3])
                p.drawText(QRectF(x, y, inner, h + 1), flags, b[2])
            elif b[0] == "phrase":
                row = self._explain.rows[b[4]] if self._explain else None
                p.setFont(b[1])
                p.setPen(b[3])
                p.drawText(QRectF(x, y, inner - btn - px * 0.5, h + 1), flags, b[2])
                r = QRectF(x + inner - btn, y, btn, btn)
                saves.append(r)
                saved = bool(row and row.saved)
                fill = QColor(ACCENT)
                fill.setAlphaF(0.25 if saved else 0.95)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(fill)
                p.drawEllipse(r)
                # «+» и галочка линиями: текстовые символы в мелком кружке выходят крошечными
                pen = QPen(TEXT if saved else QColor("#06261F"), max(1.6, btn * 0.11))
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                p.setPen(pen)
                c, k = r.center(), btn * 0.26
                if saved:
                    p.drawPolyline([QPointF(c.x() - k, c.y()), QPointF(c.x() - k * 0.25, c.y() + k * 0.7), QPointF(c.x() + k, c.y() - k * 0.7)])
                else:
                    p.drawLine(QPointF(c.x() - k, c.y()), QPointF(c.x() + k, c.y()))
                    p.drawLine(QPointF(c.x(), c.y() - k), QPointF(c.x(), c.y() + k))
            elif b[0] == "skeleton":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 30))
                p.drawRoundedRect(QRectF(x, y + h * 0.15, inner * b[1], h * 0.7), h * 0.3, h * 0.3)
            y += h + gap
        p.restore()
        self._explain_saves = saves

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
            if self.deck_label and card.phase != "saved":
                blocks.append(("text", f_note, self.deck_label, TEXT_MUTED))
        blocks.append(("text", f_note, "←→ другое слово · Shift фраза · Esc выйти", TEXT_MUTED))

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
