"""Режим слов (F3) и разбор реплики (F7): реплика нашим шрифтом поверх окна диалога, карточка, разбор.

Отдельное окно поверх игры размером с display_rect. Окно диалога игры оно закрывает своей
панелью: в ней имя, реплика и перевод, написанные нами. Рамки слов из OCR неточные (буква
мимо, слово пополам), а свои слова мы раскладываем сами и знаем, где каждое: клик попадает
ровно в слово. Остальную игру окно слегка затемняет и отвечает на все клики над ней: по
слову, по кнопкам, мимо панели (это закрывает режим, когда кнопку отпустят). Сами клики и
колесо до игры не доходят: их ещё до окон забирает хук (win/llhooks.py) и передаёт сюда в
click() и release(), а App на время режима забирает у игры фокус. Иначе игры, которые
читают мышь через Raw Input или DirectInput, пропускали бы реплику.

Окно создаётся с WS_EX_NOACTIVATE: фокус ему даёт только App. Esc, Enter и стрелки
приходят через App. Стрелками слова выбираются и без мыши: это выручает, когда игра
держит курсор у себя.

Перед тем как спрятаться, окно рисует пустой кадр: иначе при следующем F3 Windows на
миг показывала бы последний кадр (например, старый разбор реплики).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import QWidget

from lexoplay.geometry import Rect, denormalize, font_px_for
from lexoplay.ui.theme import ACCENT, FONT_FAMILY, KNOWN, NAME, RARE, STRIP_BG, TEXT, TEXT_MUTED
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
from lexoplay.words import clean

Placer = Callable[[QWidget, Rect], None]

TRANSLATION = QColor("#C3CBD8")
IDLE_MS = 30_000
PICK_MS = 350  # стрелками: значение слова запрашиваем, когда выбор остановился
HOVER_WORD_MS = 450  # наведением: курсор задержался на слове, выбираем его без клика
HOVER_ADD_MS = 900  # курсор держат на «+ в мои слова»: сохраняем без клика


def _qt_placer(widget: QWidget, rect: Rect) -> None:
    x, y, w, h = rect.to_int()
    widget.setGeometry(x, y, w, h)


@dataclass
class _Plan:
    """Раскладка панели: координаты внутри неё, считая от левого верхнего угла."""

    size: float  # кегль реплики
    pad: float
    width: float
    words: list[tuple[float, float, float, float]]  # x, y, w, h каждого слова
    name_y: float
    tr_y: float  # где начинается перевод (0: перевода нет)
    tr_h: float
    height: float


def _rect(r: QRectF) -> Rect:
    return Rect(r.x(), r.y(), r.width(), r.height())


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
        self._speaker: str | None = None
        self._translation: str | None = None
        self.translating = False  # перевод реплики ещё пишется
        self._panel: QRectF | None = None  # панель с репликой поверх окна диалога
        self._plan: _Plan | None = None
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
        self.font_family = ""  # шрифт реплики в панели; пусто = как в окне LexoPlay
        self.deck_label = ""
        self.explain_key = "F7"
        self.cursor_locked = False  # игра держит курсор: подсказка про стрелки
        self.focus_warning = False  # клик может дойти до игры: подсказка не кликать, а наводить
        self.warning_text = ""  # почему (пусто: игра не отдала фокус)
        self.rare_label = ""  # «B2+»: с какого уровня подсвечены трудные слова, для подсказки
        self._focus: int | None = None  # куда двигают стрелки
        self._close_armed = False  # нажали мимо слов (или правой): закроемся, когда кнопку отпустят
        # выбор наведением: некоторые игры ловят клик, даже когда фокус у LexoPlay, а движение мыши
        # реплику не листает. Задержал курсор на слове: оно выбрано; на «+»: сохранено
        self.hover_pick = True
        self._dwell_on: tuple[str, int] | None = None  # ("word", индекс) или ("add", 0)
        self._dwell_used: tuple[str, int] | None = None  # уже сработало: снова, только когда курсор уйдёт
        self._dwell_t0 = 0.0
        self._dwell = QTimer(self)
        self._dwell.setSingleShot(True)
        self._dwell.timeout.connect(self._dwell_done)
        self._dwell_anim = QTimer(self)  # заполнение «+», пока курсор на нём
        self._dwell_anim.setInterval(30)
        self._dwell_anim.timeout.connect(self.update)
        self._blank = False
        # плавность: панель проявляется за доли секунды, карточка слова тоже
        self.animate = False  # App включает; в тестах кадр нужен сразу
        self._alpha = 1.0
        self._card_alpha = 1.0
        self._card_word: str | None = None
        self._fade = QVariantAnimation(self)
        self._fade.setDuration(150)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.valueChanged.connect(self._on_fade)
        self._card_fade = QVariantAnimation(self)
        self._card_fade.setDuration(170)
        self._card_fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._card_fade.valueChanged.connect(self._on_card_fade)
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

    @property
    def views(self) -> list[WordView]:
        """Слова с рамками в панели (нормализованные к окну, как у OCR)."""
        return list(self._views)

    @property
    def panel(self) -> QRectF | None:
        return QRectF(self._panel) if self._panel is not None else None

    def open(
        self,
        display: Rect,
        region: Rect,
        views: list[WordView],
        strip: Rect | None = None,
        *,
        speaker: str | None = None,
        translation: str | None = None,
    ) -> None:
        self._display, self._region, self._views, self._strip = display, region, views, strip
        self._speaker, self._translation = speaker, translation
        self._sel, self._anchor, self._hover, self._card, self._focus = [], None, None, None, None
        self._explain, self._explain_open = None, False
        self.cursor_locked = False
        self.focus_warning = False
        self.warning_text = ""
        self._close_armed = False
        self._dwell_stop()
        self._dwell_used = None
        self._pick.stop()
        self._open = True
        self._card_word = None
        if self.animate:
            self._alpha = 0.0
            self._fade.stop()
            self._fade.setStartValue(0.0)
            self._fade.setEndValue(1.0)
            self._fade.start()
        else:
            self._alpha = 1.0
        self._placer(self, display)
        self.show()
        self._placer(self, display)
        self._relayout()
        self._idle.start()
        self.repaint()  # сразу свежий кадр

    def _on_fade(self, value: object) -> None:
        self._alpha = float(value)  # type: ignore[arg-type]
        self.update()

    def _on_card_fade(self, value: object) -> None:
        self._card_alpha = float(value)  # type: ignore[arg-type]
        self.update()

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
        """Те же слова заново (например, сохранённое стало жёлтым) или другой текст реплики."""
        self._views = views
        if self._sel and max(self._sel) >= len(views):
            self._sel, self._anchor, self._focus, self._card = [], None, None, None
        self._relayout()
        self.update()

    def set_translation(self, text: str | None, *, partial: bool = False) -> None:
        """Перевод реплики в панели. partial: модель ещё пишет.

        Слова при этом стоят на месте, пока перевод влезает в отведённое ему место: иначе
        реплика прыгала бы под курсором, пока перевод дописывается."""
        self._translation = text
        self.translating = partial
        plan, panel = self._plan, self._panel
        if plan is not None and panel is not None and plan.tr_y:
            fresh = self._measure(plan.size, plan.width)
            if fresh.height <= panel.height() + 0.5:
                plan.tr_h = max(plan.tr_h, fresh.tr_h)
                self.update()
                return
        self._relayout()
        self.update()

    def resizeEvent(self, e: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._relayout()

    def set_card(self, card: CardState | None) -> None:
        word = card.title if card is not None else None
        if self.animate and word is not None and word != self._card_word:
            self._card_fade.stop()  # новое слово: карточка проявляется, а не выскакивает
            self._card_alpha = 0.0
            self._card_fade.setStartValue(0.0)
            self._card_fade.setEndValue(1.0)
            self._card_fade.start()
        self._card_word = word
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
        self._dwell_stop()
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

    def _fonts(self, size: float) -> tuple[QFont, QFont, QFont]:
        """Реплика, имя, перевод."""
        family = self.font_family or FONT_FAMILY

        def make(px: float, weight: QFont.Weight) -> QFont:
            f = QFont(family)
            f.setPixelSize(max(9, round(px)))
            f.setWeight(weight)
            return f

        return make(size, QFont.Weight.Medium), make(size * 0.74, QFont.Weight.DemiBold), make(size * 0.76, QFont.Weight.Normal)

    def _translation_text(self) -> str:
        if self._translation:
            return self._translation + (" …" if self.translating else "")
        return "Перевожу…" if self.translating else ""

    def _measure(self, size: float, width: float) -> _Plan:
        f_text, f_name, f_tr = self._fonts(size)
        fm, fm_name, fm_tr = QFontMetricsF(f_text), QFontMetricsF(f_name), QFontMetricsF(f_tr)
        pad = max(10.0, size * 0.6)
        inner = max(40.0, width - 2 * pad)
        y = pad
        name_y = y
        if self._speaker:
            y += fm_name.height() + size * 0.2
        line_h = fm.height() * 1.16
        space = fm.horizontalAdvance(" ")
        x, row = 0.0, 0
        words: list[tuple[float, float, float, float]] = []
        for v in self._views:
            w = min(fm.horizontalAdvance(v.text), inner)
            if x > 0 and x + w > inner:  # не влезает: на новую строку
                row += 1
                x = 0.0
            words.append((pad + x, y + row * line_h, w, fm.height()))
            x += w + space
        if self._views:
            y += (row + 1) * line_h
        tr_y = tr_h = 0.0
        tr = self._translation_text()
        if tr:
            y += size * 0.45
            tr_y = y
            tr_h = fm_tr.boundingRect(QRectF(0, 0, inner, 10000), int(Qt.TextFlag.TextWordWrap), tr).height()
            if self.translating:  # перевод ещё пишется: место под него сразу, по числу строк реплики
                tr_h = max(tr_h, (row + 1) * fm_tr.lineSpacing())
            y += tr_h
        return _Plan(size, pad, width, words, name_y, tr_y, tr_h, y + pad)

    def _relayout(self) -> None:
        """Разложить реплику по панели поверх окна диалога и дать словам точные рамки."""
        bounds = self._bounds()
        if bounds.w < 50 or bounds.h < 50:
            return
        reg = denormalize(self._region, bounds)
        base = reg
        strip = self._strip
        if strip is not None and strip.bottom >= reg.y - 40 and strip.y <= reg.bottom + 40:
            base = union([reg, strip])  # строка перевода вплотную к окну диалога: панель накрывает и её
        px = self._px()
        left = max(8.0, base.x)
        width = max(240.0, min(base.right, bounds.w - 8) - left)
        size = min(px * 2.4, max(px * 1.2, reg.h * 0.16))  # примерно кегль самой игры
        room = max(base.h, bounds.h * 0.5)
        plan = self._measure(size, width)
        for _ in range(8):  # длинная реплика: шрифт помельче, пока не влезет
            if plan.height <= room or size <= px * 0.9:
                break
            size *= 0.9
            plan = self._measure(size, width)
        h = max(base.h, plan.height)
        top = base.bottom - h if base.y + base.h / 2 > bounds.h / 2 else base.y  # окно внизу: растём вверх
        top = min(max(8.0, top), max(8.0, bounds.h - 8 - h))
        shift = (h - plan.height) / 2  # панель выше текста (закрывает всё окно диалога): текст посередине
        if shift > 0:
            plan.words = [(x, y + shift, w, hh) for x, y, w, hh in plan.words]
            plan.name_y += shift
            if plan.tr_y:
                plan.tr_y += shift
        self._panel = QRectF(left, top, width, h)
        self._plan = plan
        W, H = bounds.w, bounds.h
        self._views = [
            replace(v, rect=Rect((left + x) / W, (top + y) / H, w / W, hh / H))
            for v, (x, y, w, hh) in zip(self._views, plan.words, strict=True)
        ]

    def _avoid(self) -> Rect:
        """Что карточка и разбор не закрывают: панель с репликой (и строку перевода)."""
        if self._panel is not None:
            rects = [_rect(self._panel)]
        else:
            rects = [denormalize(self._region, self._bounds())]
        if self._strip is not None:
            rects.append(self._strip)
        return union(rects)

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
        self.hover(e.position())

    def hover(self, pos: QPointF) -> None:
        """Курсор над слоем (движение мыши до окна доходит: его хук не забирает)."""
        if not self._open:
            return
        self._idle.start()
        hover = self._hit_word(pos)
        over_button = bool(self._button_rect and self._button_rect.contains(pos))
        self.setCursor(Qt.CursorShape.PointingHandCursor if hover is not None or over_button else Qt.CursorShape.ArrowCursor)
        if hover != self._hover:
            self._hover = hover
            self.update()
        if not self.hover_pick:
            return
        target: tuple[str, int] | None = None
        if over_button and self._card is not None and self._card.button_enabled:
            target = ("add", 0)
        elif hover is not None and not (self._sel == [hover] and not self._shift_down()):
            target = ("word", hover)
        if target != self._dwell_used:
            self._dwell_used = None
        elif target is not None:
            return  # «+» уже нажат удержанием: второй раз то же слово не сохраняем
        if target != self._dwell_on:
            self._dwell_stop()
            if target is not None:
                self._dwell_on = target
                self._dwell_t0 = time.monotonic()
                self._dwell.start(HOVER_ADD_MS if target[0] == "add" else HOVER_WORD_MS)
                if target[0] == "add":
                    self._dwell_anim.start()

    def _dwell_stop(self) -> None:
        self._dwell.stop()
        self._dwell_anim.stop()
        if self._dwell_on is not None and self._dwell_on[0] == "add":
            self.update()
        self._dwell_on = None

    def _dwell_done(self) -> None:
        target = self._dwell_on
        self._dwell_stop()
        if not self._open or target is None:
            return
        if target[0] == "add":
            self._dwell_used = target
            if self._card and self._card.button_enabled:
                self.add_clicked.emit()
        else:
            self._pick_word(target[1], shift=self._shift_down())

    @staticmethod
    def _shift_down() -> bool:
        return bool(QGuiApplication.queryKeyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        e.accept()  # клик дальше в игру не идёт (если хук не стоит: см. click)
        if e.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self.click(e.position(), right=e.button() == Qt.MouseButton.RightButton, shift=shift)
        elif self._open:
            self._idle.start()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        e.accept()
        if e.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            self.release()

    def click(self, pos: QPointF, *, right: bool = False, shift: bool = False) -> None:
        """Нажатие кнопки над слоем в точке pos (логические пиксели окна).

        Обычно приходит от хука: он забрал клик у игры, и до окна тот уже не дошёл."""
        self._close_armed = False
        if not self._open:
            return
        self._idle.start()
        if right:
            self._close_armed = True
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
            if self._panel is not None and self._panel.contains(pos):
                return  # между словами: промахнулся, режим не закрываем
            self._close_armed = True  # мимо панели: закрыть режим, в игру клик не попадёт
            return
        self._pick_word(idx, shift=shift)

    def _pick_word(self, idx: int, *, shift: bool) -> None:
        """Выбрать слово (кликом или наведением); с Shift фраза от прошлого слова до этого."""
        self._dwell_stop()
        self._explain_open = False  # слово важнее: разбор спрячется, F7 вернёт
        self._pick.stop()
        sel = selection_range(self._anchor, idx) if shift else [idx]
        if not shift:
            self._anchor = idx
        self._focus = idx
        self._sel = sel
        self.update()
        self.selected.emit(sel)

    def release(self) -> None:
        """Кнопку отпустили. Закрываемся только теперь: отдай App фокус игре на нажатии, её
        отпускание досталось бы игре, а многие новеллы листают реплику как раз по отпусканию."""
        if self._close_armed and self._open:
            self._close_armed = False
            self.finish("outside")

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
        p.fillRect(self.rect(), QColor(0, 0, 0, 46))  # ловим все клики над игрой и чуть затемняем её
        if self._alpha < 1.0:
            p.setOpacity(max(0.0, self._alpha))
        avoid = self._avoid()

        px = self._px()
        plan = self._plan
        size = plan.size if plan else px
        line_w = max(1.5, size / 14)
        f_text, f_name, f_tr = self._fonts(size)
        fm = QFontMetricsF(f_text)
        panel = self._panel
        if panel is not None and plan is not None:
            bg = QColor(STRIP_BG)
            bg.setAlphaF(0.97)
            p.setPen(QPen(QColor(255, 255, 255, 30), 1))
            p.setBrush(bg)
            p.drawRoundedRect(panel, 14, 14)
            if self._speaker:
                p.setFont(f_name)
                p.setPen(NAME)
                p.drawText(QPointF(panel.x() + plan.pad, panel.y() + plan.name_y + QFontMetricsF(f_name).ascent()), self._speaker)
            tr = self._translation_text()
            if tr:
                sep_y = panel.y() + plan.tr_y - size * 0.24
                p.setPen(QPen(QColor(255, 255, 255, 26), 1))
                p.drawLine(QPointF(panel.x() + plan.pad, sep_y), QPointF(panel.right() - plan.pad, sep_y))
                p.setFont(f_tr)
                p.setPen(TRANSLATION if self._translation else TEXT_MUTED)
                box = QRectF(panel.x() + plan.pad, panel.y() + plan.tr_y, panel.width() - 2 * plan.pad, plan.tr_h + 2)
                p.drawText(box, int(Qt.TextFlag.TextWordWrap), tr)
        explained: set[int] = set()
        if self._explain_open and self._explain and self._explain.phase == "ready":
            for row in self._explain.rows:
                explained.update(row.indices)
            self._paint_phrase_lines(p, line_w, fm)
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
            elif self._rare(v) and v.index not in explained:  # трудное слово: как маркером
                x0, x1 = self._core_span(v.text, r, fm)
                fill = QColor(RARE)
                fill.setAlphaF(0.16)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(fill)
                p.drawRoundedRect(QRectF(x0 - 1.5, r.y() - 1, x1 - x0 + 3, r.height() + 2), 3, 3)  # соседние не сливаются
            p.setFont(f_text)
            p.setPen(TEXT)
            p.drawText(QPointF(r.x(), r.y() + fm.ascent()), v.text)
            if v.index in explained:
                continue  # выражение из разбора подчёркнуто целиком (_paint_phrase_lines)
            if not v.clickable or v.stop:
                continue
            y = r.y() + fm.ascent() + fm.descent() * 0.55 + line_w
            x0, x1 = self._core_span(v.text, r, fm)  # под самим словом, без запятой и кавычек
            if v.known:
                pen = QPen(KNOWN, line_w * 1.2)
            elif self._rare(v):
                pen = QPen(RARE, line_w * 1.3)
            else:
                c = QColor(ACCENT)
                c.setAlphaF(0.35 if v.tier in ("common", "name") else 0.85)  # частое и имена еле видно
                pen = QPen(c, line_w)
                pen.setStyle(Qt.PenStyle.DotLine)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawLine(QPointF(x0, y), QPointF(x1, y))

        self._button_rect = None
        self._card_rect = None
        self._explain_rect = None
        self._explain_saves = []
        self._explain_btn = None
        self._retranslate_btn = None
        if self._explain_open and self._explain is not None:
            self._paint_explain(p, self._explain, avoid, bounds, px)
        elif self._card and self._sel:
            sel = union([denormalize(self._views[i].rect, bounds) for i in self._sel if i < len(self._views)])
            if self._card_alpha < 1.0:
                p.save()
                p.setOpacity(p.opacity() * max(0.0, self._card_alpha))
                p.translate(0, (1.0 - self._card_alpha) * 8)  # и чуть всплывает
                self._paint_card(p, self._card, sel, bounds, px, avoid)
                p.restore()
            else:
                self._paint_card(p, self._card, sel, bounds, px, avoid)
        if not self._explain_open:
            self._paint_explain_button(p, avoid, bounds, px)
        p.end()

    @staticmethod
    def _rare(v: WordView) -> bool:
        return v.tier == "rare" and v.clickable and not v.stop and not v.known

    @staticmethod
    def _core_span(text: str, r: QRectF, fm: QFontMetricsF) -> tuple[float, float]:
        """Где в слове сами буквы: «school,» подчёркиваем без запятой."""
        core = clean(text)
        at = text.find(core) if core else -1
        if at < 0:
            return r.left(), r.right()
        x0 = r.left() + fm.horizontalAdvance(text[:at])
        return x0, min(r.right(), x0 + fm.horizontalAdvance(core))

    def _paint_phrase_lines(self, p: QPainter, line_w: float, fm: QFontMetricsF) -> None:
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
                    y = max(x.y() for x in run) + fm.ascent() + fm.descent() * 0.55 + line_w
                    p.drawLine(QPointF(run[0].left() - 2, y), QPointF(run[-1].right() + 2, y))
                    run = []
                if r is not None:
                    run.append(r)

    def _paint_explain_button(self, p: QPainter, region: Rect, bounds: Rect, px: float) -> None:
        """Кнопки над панелью справа: «Перевести иначе» и «Разбор реплики». region: панель (и строка перевода)."""
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
        elif self.focus_warning:
            text = self.warning_text or "Игра не отдала фокус: не кликай, наводи на слово и держи курсор на «+»"
        else:
            text = ("Наведи на слово" if self.hover_pick else "Клик или ←→ по слову") + " · Shift фраза · Esc выйти"
        x = max(4.0, region.x)
        room = right - x - px * 0.6
        if room < px * 8:
            return
        # что значат цвета: только те, что есть в этой реплике
        legend: list[tuple[QColor, str]] = []
        if not (self.cursor_locked or self.focus_warning):
            if any(v.known and v.clickable and not v.stop for v in self._views):
                legend.append((KNOWN, "твои"))
            if any(self._rare(v) for v in self._views):
                legend.append((RARE, f"редкие {self.rare_label}".strip()))
        swatch, gap = px * 0.9, px * 0.35
        legend_w = sum(swatch + gap + fm.horizontalAdvance(label) + px * 0.8 for _c, label in legend)
        if legend and room - legend_w < px * 10:
            legend, legend_w = [], 0.0  # не влезает: подсказка важнее
        text = fm.elidedText(text, Qt.TextElideMode.ElideRight, room - px * 1.4 - legend_w)
        text_w = fm.horizontalAdvance(text)
        w = text_w + px * 1.4 + legend_w
        rect = QRectF(x, y, w, h)
        bg = QColor(STRIP_BG)
        bg.setAlphaF(0.88)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(rect, h / 2, h / 2)
        p.setFont(f)
        p.setPen(QColor("#F2C94C") if self.cursor_locked or self.focus_warning else TEXT_MUTED)
        mid = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        tx = x + px * 0.7
        p.drawText(QRectF(tx, y, text_w + 1, h), mid, text)
        tx += text_w
        for color, label in legend:
            tx += px * 0.8
            pen = QPen(color, max(2.0, px / 9))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            ly = y + h / 2 + fm.ascent() * 0.38
            p.drawLine(QPointF(tx, ly), QPointF(tx + swatch, ly))
            tx += swatch + gap
            p.setPen(color)
            lw = fm.horizontalAdvance(label)
            p.drawText(QRectF(tx, y, lw + 1, h), mid, label)
            tx += lw

    def _paint_explain(self, p: QPainter, st: ExplainState, region: Rect, bounds: Rect, px: float) -> None:
        """Разбор реплики над панелью. region: панель (и строка перевода), её разбор не закрывает."""
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
        box = place_card(region, card_w, card_h, bounds, gap=max(8.0, px * 0.6), avoid=region)
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

    def _paint_card(self, p: QPainter, card: CardState, sel: Rect, bounds: Rect, px: float, avoid: Rect) -> None:
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
                if self._dwell_on == ("add", 0):  # курсор держат на кнопке: заполняется до сохранения
                    k = min(1.0, (time.monotonic() - self._dwell_t0) * 1000 / HOVER_ADD_MS)
                    p.save()
                    p.setClipRect(QRectF(btn.x(), btn.y(), btn.width() * k, btn.height()))
                    p.setBrush(QColor(255, 255, 255, 90))
                    p.drawRoundedRect(btn, btn_h / 2, btn_h / 2)
                    p.restore()
                p.setFont(f_btn)
                p.setPen(QColor("#06261F"))
                p.drawText(btn, int(Qt.AlignmentFlag.AlignCenter), label)
                p.setFont(f_note)
                p.setPen(TEXT_MUTED)
                p.drawText(
                    QRectF(btn.right() + px * 0.6, btn.y(), inner - bw - px * 0.6, btn_h),
                    int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                    "наведи и подержи или Enter" if self.hover_pick else "Enter",
                )
            y += h + gap
