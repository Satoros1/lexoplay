"""Оверлей поверх игры: строка перевода над окном диалога и тосты в углу.

Одно прозрачное окно размером с display_rect, клики проходят насквозь, фокус не берёт.
Ставится в физических координатах через `placer` (на Windows это SetWindowPos), а рисует
всё в логических пикселях относительно своего размера, поэтому DPI и Magpie не мешают.

Плашка появляется и исчезает плавно (доли секунды), а пока идёт перевод, в ней бегут
три точки. Логически она скрыта сразу: исчезающая копия только дорисовывается.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from PySide6.QtCore import QEasingCurve, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from lexoplay.geometry import Rect, denormalize, font_px_for, place_strip
from lexoplay.recall import RecallChip
from lexoplay.ui.theme import ACCENT, FONT_FAMILY, KNOWN, STRIP_BG, TEXT, TEXT_MUTED

Placer = Callable[[QWidget, Rect], None]

FADE_IN_MS = 120
FADE_OUT_MS = 140
BOX_MS = 140  # плашка выросла или сжалась
REVEAL_PARTS = 5  # дописанный кусок перевода проявляется примерно за столько кадров
DOTS_MS = 300  # шаг бегущих точек, пока идёт перевод


@dataclass(frozen=True, slots=True)
class _Strip:
    """Что нарисовано в плашке: нужно, чтобы дорисовать её, пока она исчезает."""

    region: Rect | None
    text: str | None  # None: идёт перевод (точки)
    original: str | None
    recall: tuple[RecallChip, ...] = ()  # сохранённые слова из этой реплики: строка подсказок под переводом
    hidden: bool = False  # учёба: перевод спрятан, вместо плашки маленькая «Alt · перевод»


@dataclass(frozen=True, slots=True)
class _ChipBox:
    rect: QRectF
    word: str
    translation: str
    count: str  # «×3», если слово встречалось не первый раз


@dataclass(frozen=True, slots=True)
class _RecallRow:
    label: QRectF | None  # «уже сохранял» перед подсказками (если влезает)
    chips: tuple[_ChipBox, ...]
    font: QFont
    bold: QFont
    height: float


RECALL_LABEL = "уже сохранял"
STUDY_HINT = "Alt · перевод"  # учёба: перевод спрятан, виден по Alt или наведению
GLOSS_MIN_PX = 11.0
GLOSS_MAX_PX = 22.0
PAGE_FADE_S = 0.18  # новый перевод абзаца проявляется за столько
PAGE_MIN_PX = 12.0
PAGE_SAME_SIZE = 0.8  # общий кегль страницы не мельче стольких от кегля игры


@dataclass(frozen=True, slots=True)
class PageView:
    """Абзац страницы (текст на весь экран) и его перевод поверх него."""

    rect: Rect  # абзац в долях окна игры (как region)
    line_h: float  # высота строки оригинала в долях окна: от неё размер шрифта перевода
    text: str | None  # None: ещё переводится (точки)
    error: str | None = None


@dataclass(frozen=True, slots=True)
class GlossView:
    """Учёба: подсказка над словом в окне диалога игры."""

    rect: Rect  # слово в долях окна игры (как region)
    text: str  # значение
    kind: str  # new: значение видно | test: «?», вспомни сам | saved: «?» сохранённого слова
    label: str = ""  # сохранённое выражение целиком: видно, когда «?» открыт
    open: bool = False  # «?» открыт наведением

    @property
    def shown(self) -> str:
        if self.kind == "new" or self.open:
            return f"{self.label} · {self.text}" if self.label and self.open else self.text
        return "?"


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
        self._original: str | None = None  # английский оригинал над переводом (по желанию)
        self._recall: tuple[RecallChip, ...] = ()
        # режим слов открыт: его панель сама показывает перевод, а плашка под ней только мешала бы
        # (два окна поверх всех спорят, кто выше, и плашка мигала поверх панели)
        self._muted = False
        self._busy = False
        self._toast: str | None = None
        self.font_scale = 1.0
        self.opacity = 0.84
        self.show_original = False
        self.strip_place = "above"  # above | below | top
        self.strip_custom: Rect | None = None  # своё место для игры: x, y, ширина в долях окна
        self.font_family = ""
        self.font_bold = False
        self.fade = True  # плавное появление и исчезание плашки
        self._blank = False  # пустой кадр перед скрытием: при следующем показе не мелькнёт старый
        self._alpha = 1.0
        self._ghost: _Strip | None = None  # исчезающая плашка
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_fade)
        self._anim.finished.connect(self._fade_done)
        self._dots = 0
        # плашка меняет размер (перевод дописывается, добавилась строка) плавно, а не скачком
        self._box_anim = QVariantAnimation(self)
        self._box_anim.setDuration(BOX_MS)
        self._box_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._box_anim.setStartValue(0.0)
        self._box_anim.setEndValue(1.0)
        self._box_anim.valueChanged.connect(lambda _v: self.update())
        self._box_from: QRectF | None = None
        self._box_to: QRectF | None = None
        self._reveal = 0  # сколько букв перевода уже видно (остальные проявляются)
        self._reveal_timer = QTimer(self)
        self._reveal_timer.setInterval(16)
        self._reveal_timer.timeout.connect(self._reveal_tick)
        self._dots_timer = QTimer(self)
        self._dots_timer.setInterval(DOTS_MS)
        self._dots_timer.timeout.connect(self._tick_dots)
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._clear_toast)
        # текст на весь экран (NVL): перевод поверх каждого абзаца вместо плашки
        self._page: tuple[PageView, ...] = ()
        self._page_born: dict[int, float] = {}  # абзац -> когда пришёл его перевод (для проявления)
        self._peek = False  # держат Alt: виден оригинал под переводом
        self._page_timer = QTimer(self)
        self._page_timer.setInterval(16)
        self._page_timer.timeout.connect(self._page_tick)
        # учёба: подсказки над словами игры, перевод спрятан, пока не держат Alt и не наводят на плашку
        self._glosses: tuple[GlossView, ...] = ()
        self.study_hide = False
        self._study_open = False
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

    @property
    def recall(self) -> tuple[RecallChip, ...]:
        return self._recall

    def show_busy(self, region: Rect, original: str | None = None, recall: Sequence[RecallChip] = ()) -> None:
        if self._page:
            self.hide_page()
        appearing = not self.strip_visible
        self._region, self._text, self._busy = region, None, True
        self._original = original if original and original.strip() else None
        self._recall = tuple(recall)
        if appearing or not self._dots_timer.isActive():
            self._dots = 0
            self._dots_timer.start()
        self._appear(appearing)
        self._refresh()

    def show_text(
        self, region: Rect, text: str, original: str | None = None, recall: Sequence[RecallChip] = ()
    ) -> None:
        appearing = not self.strip_visible
        before = self._text
        self._region, self._text, self._busy = region, text, False
        self._original = original if original and original.strip() else None
        self._recall = tuple(recall)
        self._dots_timer.stop()
        # перевод дописывается кусками: новые буквы проявляются за доли секунды, а не скачком.
        # Готовый перевод (из кеша, сразу целиком) показываем сразу
        if appearing or not self.fade or (before is not None and not text.startswith(before[: min(len(before), 8)])):
            self._reveal = len(text)
        else:
            common = 0
            for a, b in zip(before or "", text, strict=False):
                if a != b:
                    break
                common += 1
            self._reveal = min(self._reveal, common)
            if self._reveal < len(text):
                self._reveal_timer.start()
        self._appear(appearing)
        self._refresh()

    def _reveal_tick(self) -> None:
        text = self._text or ""
        left = len(text) - self._reveal
        if left <= 0:
            self._reveal_timer.stop()
            return
        self._reveal = min(len(text), self._reveal + max(2, -(-left // REVEAL_PARTS)))
        self.update()

    @property
    def muted(self) -> bool:
        return self._muted

    def mute_strip(self, on: bool) -> None:
        """Спрятать плашку, не забывая её: после режима слов она вернётся, если ещё нужна."""
        if self._muted == on:
            return
        self._muted = on
        if on:
            self._anim.stop()
            self._ghost = None
            self._alpha = 1.0
        self._refresh()

    def hide_strip(self) -> None:
        if self.strip_visible and self.isVisible() and self.fade and not self._muted:
            self._ghost = self._shown()
            self._fade_to(0.0, FADE_OUT_MS)
        self._text, self._busy, self._original, self._recall = None, False, None, ()
        self._glosses, self._study_open = (), False  # подсказки принадлежат реплике
        self._dots_timer.stop()
        self._refresh()

    # --- учёба ---
    @property
    def glosses(self) -> tuple[GlossView, ...]:
        return self._glosses

    def show_glosses(self, glosses: Sequence[GlossView]) -> None:
        glosses = tuple(glosses)
        if glosses != self._glosses:
            self._glosses = glosses
            self._refresh()

    def set_study_open(self, on: bool) -> None:
        """Учёба: перевод виден (держат Alt или курсор на плашке) или снова спрятан."""
        if self._study_open != on:
            self._study_open = on
            self.update()

    @property
    def study_open(self) -> bool:
        return self._study_open

    def _gloss_font(self, size: float, *, bold: bool = False) -> QFont:
        f = QFont(self.font_family or FONT_FAMILY)
        f.setPixelSize(max(1, round(size)))
        f.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Medium)
        return f

    def gloss_boxes(self) -> list[tuple[int, QRectF, QRectF, QFont]]:
        """(номер подсказки, плашка значения, слово, шрифт) в пикселях окна. Плашка над словом по центру;
        соседние в одной строке не налезают друг на друга."""
        w, h = float(self.width()), float(self.height())
        bounds = Rect(0, 0, w, h)
        items: list[tuple[int, QRectF, QRectF, QFont]] = []
        for i, g in enumerate(self._glosses):
            text = g.shown
            if not text:
                continue
            r = denormalize(g.rect, bounds)
            word_h = max(8.0, r.h)
            size = max(GLOSS_MIN_PX, min(GLOSS_MAX_PX, word_h * 0.5)) * self.font_scale
            font = self._gloss_font(size, bold=text == "?")
            fm = QFontMetricsF(font)
            pad = size * (0.55 if text == "?" else 0.42)
            bw = fm.horizontalAdvance(text) + 2 * pad
            bh = fm.height() + size * 0.1
            bx = r.x + r.w / 2 - bw / 2
            by = r.y - bh - max(1.0, word_h * 0.05)
            if by < 2:
                by = r.bottom + 2  # над словом места нет: под ним
            items.append((i, QRectF(bx, by, bw, bh), QRectF(r.x, r.y, r.w, r.h), font))
        items.sort(key=lambda it: (round(it[1].y() / max(1.0, it[1].height())), it[1].x()))
        out: list[tuple[int, QRectF, QRectF, QFont]] = []
        for i, box, word, font in items:
            prev = out[-1][1] if out else None
            if prev is not None and abs(prev.y() - box.y()) < box.height() * 0.6 and box.x() < prev.right() + 4:
                box.moveLeft(prev.right() + 4)
            box.moveLeft(max(2.0, min(box.x(), w - box.width() - 2)))
            out.append((i, box, word, font))
        return out

    def gloss_at(self, nx: float, ny: float) -> int | None:
        """Подсказка под точкой (в долях окна игры): над её словом или на самом слове."""
        x, y = nx * self.width(), ny * self.height()
        for i, box, word, _font in self.gloss_boxes():
            if box.adjusted(-3, -3, 3, 3).contains(x, y) or word.adjusted(-2, -2, 2, 2).contains(x, y):
                return i
        return None

    def strip_hit(self, nx: float, ny: float, margin: float = 6.0) -> bool:
        """Курсор на плашке перевода: на спрятанной «Alt · перевод» или на открытом переводе."""
        if not self.strip_visible:
            return False
        x, y = nx * self.width(), ny * self.height()
        state = self._shown()
        rects = [self._strip_geometry(state)[0]]
        if self.study_hide:
            rects.append(self._strip_geometry(replace(state, hidden=not state.hidden))[0])
        return any(r.adjusted(-margin, -margin, margin, margin).contains(x, y) for r in rects)

    # --- страница (текст на весь экран) ---
    @property
    def page_visible(self) -> bool:
        return bool(self._page)

    @property
    def page(self) -> tuple[PageView, ...]:
        return self._page

    def show_page(self, blocks: Sequence[PageView]) -> None:
        """Перевод поверх абзацев. Плашка в это время не нужна: перевод прямо на месте текста."""
        old = self._page
        blocks = tuple(blocks)
        now = time.monotonic()
        born: dict[int, float] = {}
        for i, b in enumerate(blocks):
            same = i < len(old) and old[i].text is not None and old[i].text == b.text
            if b.text is not None:
                # уже был виден тот же перевод: без проявления; новый перевод проявляется
                born[i] = self._page_born.get(i, 0.0) if same else (0.0 if not self.fade else now)
        self._page, self._page_born = blocks, born
        if self._text is not None or self._busy:
            self._text, self._busy, self._original, self._recall = None, False, None, ()
        if any(b.text is None for b in blocks):
            if not self._dots_timer.isActive():
                self._dots_timer.start()
        self._page_timer.start()
        self._refresh()

    def hide_page(self) -> None:
        self._page, self._page_born, self._peek = (), {}, False
        self._page_timer.stop()
        self._refresh()

    def set_peek(self, on: bool) -> None:
        """Пока держат клавишу: перевод не рисуем, виден оригинал игры."""
        if self._peek != on:
            self._peek = on
            self.update()

    @property
    def peeking(self) -> bool:
        return self._peek

    def _page_tick(self) -> None:
        now = time.monotonic()
        if any(now - t < PAGE_FADE_S for t in self._page_born.values()):
            self.update()
        elif not any(b.text is None for b in self._page):
            self._page_timer.stop()
            self.update()

    def page_boxes(self) -> list[tuple[QRectF, QRectF, QFont]]:
        """(фон абзаца, где текст, шрифт) в пикселях окна, для каждого абзаца по порядку.

        Шрифт на странице один для всех абзацев: самый мелкий из тех, что влезли, но не мельче
        PAGE_SAME_SIZE от кегля игры. Абзац, которому и так тесно, мельчает сам по себе."""
        w, h = float(self.width()), float(self.height())
        bounds = Rect(0, 0, w, h)
        family = self.font_family or FONT_FAMILY
        rects = [denormalize(b.rect, bounds) for b in self._page]
        plans = []
        for i, (b, r) in enumerate(zip(self._page, rects, strict=True)):
            line_px = max(8.0, b.line_h * h)
            # рамка слова от OCR примерно с кегль игры: перевод того же размера, что и оригинал
            px = max(PAGE_MIN_PX, min(44.0, line_px * 0.95)) * self.font_scale
            pad = max(4.0, line_px * 0.28)
            text_w = max(60.0, r.w)
            # вниз можно занять место до следующего абзаца: русский текст обычно длиннее английского
            below = rects[i + 1].y - pad * 1.4 if i + 1 < len(rects) else h - pad
            room = max(r.h, below - r.y)
            value = b.text if b.text is not None else "…"
            if b.error:
                value = "Не перевелось: " + b.error
            plans.append((b, r, line_px, px, pad, text_w, room, value, self._fit_page(family, value, text_w, room, px)))
        fitted = [plan[8] for plan in plans if plan[0].text is not None]
        base = [plan[3] for plan in plans]
        common = max(min(fitted), min(base) * PAGE_SAME_SIZE) if fitted else None
        out: list[tuple[QRectF, QRectF, QFont]] = []
        for b, r, line_px, _px, pad, text_w, _room, value, size in plans:
            if common is not None:
                size = min(size, common)
            font = self._page_font(family, size)
            br = QFontMetricsF(font).boundingRect(QRectF(0, 0, text_w, 100000), Qt.TextFlag.TextWordWrap, value)
            text_h = br.height() if b.text is not None else max(r.h, line_px)
            box_h = max(r.h, text_h)
            text_rect = QRectF(r.x, r.y, text_w, box_h)
            box = QRectF(r.x - pad, r.y - pad * 0.7, text_w + 2 * pad, box_h + pad * 1.4)
            out.append((box, text_rect, font))
        return out

    def _page_font(self, family: str, size: float) -> QFont:
        font = QFont(family)
        font.setWeight(QFont.Weight.Bold if self.font_bold else QFont.Weight.Medium)
        font.setPixelSize(max(1, round(size)))
        return font

    def _fit_page(self, family: str, value: str, width: float, room: float, size: float) -> float:
        """Кегль, при котором перевод абзаца влезает в его место (не мельче PAGE_MIN_PX)."""
        while True:
            br = QFontMetricsF(self._page_font(family, size)).boundingRect(
                QRectF(0, 0, width, 100000), Qt.TextFlag.TextWordWrap, value
            )
            if br.height() <= room or size <= PAGE_MIN_PX:
                return size
            size = max(PAGE_MIN_PX, size * 0.92)

    def _paint_page(self, p: QPainter) -> None:
        now = time.monotonic()
        bg = QColor(STRIP_BG)
        bg.setAlphaF(1.0)  # английский под переводом не должен просвечивать (оригинал виден по Alt)
        for i, (b, (box, text_rect, font)) in enumerate(zip(self._page, self.page_boxes(), strict=True)):
            born = self._page_born.get(i)
            alpha = 1.0 if born is None or born <= 0 else max(0.0, min(1.0, (now - born) / PAGE_FADE_S))
            p.setOpacity(1.0)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            radius = min(10.0, box.height() / 4)
            p.drawRoundedRect(box, radius, radius)
            if b.text is None and not b.error:
                self._paint_dots(p, QRectF(text_rect.x(), text_rect.y(), text_rect.width(), font.pixelSize() * 1.3), font)
                continue
            p.setOpacity(alpha)
            p.setFont(font)
            p.setPen(TEXT_MUTED if b.error else TEXT)
            value = ("Не перевелось: " + b.error) if b.error else (b.text or "")
            p.drawText(text_rect, int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop), value)
        p.setOpacity(1.0)

    # --- плавность ---
    def _box(self, box: QRectF) -> QRectF:
        """Где рисовать плашку сейчас: если её размер поменялся, она доезжает до нового за BOX_MS."""
        if not self.fade or self._box_to is None:
            self._box_from = self._box_to = QRectF(box)
            return box
        if box != self._box_to:
            self._box_from = self._box_now()
            self._box_to = QRectF(box)
            self._box_anim.stop()
            self._box_anim.start()
        return self._box_now()

    def _box_now(self) -> QRectF:
        a, b = self._box_from, self._box_to
        assert b is not None
        if a is None or self._box_anim.state() != QVariantAnimation.State.Running:
            return QRectF(b)
        t = float(self._box_anim.currentValue())
        return QRectF(
            a.x() + (b.x() - a.x()) * t,
            a.y() + (b.y() - a.y()) * t,
            a.width() + (b.width() - a.width()) * t,
            a.height() + (b.height() - a.height()) * t,
        )

    def _appear(self, appearing: bool) -> None:
        if appearing and self._ghost is None:
            self._box_to = None  # новая плашка появляется на своём месте, а не едет от прошлой
        if self._ghost is not None:  # плашка как раз исчезала: возвращаем её с той же прозрачности
            self._ghost = None
            self._fade_to(1.0, FADE_IN_MS)
        elif appearing:
            if self.fade:
                self._alpha = 0.0
                self._fade_to(1.0, FADE_IN_MS)
            else:
                self._anim.stop()
                self._alpha = 1.0

    def _fade_to(self, target: float, ms: int) -> None:
        self._anim.stop()
        if not self.fade:
            self._alpha = target
            self._fade_done()
            return
        self._anim.setStartValue(self._alpha)
        self._anim.setEndValue(target)
        self._anim.setDuration(max(1, round(ms * abs(target - self._alpha))))
        self._anim.start()

    def _on_fade(self, value: object) -> None:
        self._alpha = float(value)  # type: ignore[arg-type]
        self.update()

    def _fade_done(self) -> None:
        if self._ghost is not None and self._alpha <= 0.01:
            self._ghost = None
            self._refresh()

    def _tick_dots(self) -> None:
        page_busy = any(b.text is None and not b.error for b in self._page)
        if (not self._busy or self._text is not None) and not page_busy:
            self._dots_timer.stop()
            return
        self._dots = (self._dots + 1) % 3
        self.update()

    @property
    def text(self) -> str | None:
        return self._text

    def strip_rect(self) -> Rect | None:
        """Где сейчас строка перевода, в координатах окна (оно совпадает с display_rect)."""
        if not self.strip_visible:
            return None
        box = self._strip_geometry(self._shown())[0]
        return Rect(box.x(), box.y(), box.width(), box.height())

    def _shown(self) -> _Strip:
        return _Strip(self._region, self._text, self._original, self._recall, self.study_hide and not self._study_open)

    def toast(self, text: str, ms: int = 2400) -> None:
        self._toast = text
        self._toast_timer.start(ms)
        self._refresh()

    def _clear_toast(self) -> None:
        self._toast = None
        self._refresh()

    def _refresh(self) -> None:
        need = (
            (not self._muted and (self.strip_visible or self._ghost is not None or bool(self._page) or bool(self._glosses)))
            or self._toast is not None
        )
        if need:
            if not self.isVisible():
                self._placer(self, self._display)
                self.show()
                self._placer(self, self._display)
            self.update()
        elif self.isVisible():
            self._blank = True
            self.repaint()
            self.hide()
            self._blank = False

    # --- отрисовка ---
    def _original_shown(self, strip: _Strip) -> str | None:
        return strip.original if self.show_original else None

    def _strip_geometry(
        self, strip_state: _Strip
    ) -> tuple[QRectF, QFont, QRectF, QFont, QRectF | None, _RecallRow | None]:
        """(плашка, шрифт перевода, где перевод, шрифт оригинала, где оригинал или None, подсказки или None)."""
        text = strip_state.text if strip_state.text is not None else "…"
        w, h = float(self.width()), float(self.height())
        bounds = Rect(0, 0, w, h)
        region = denormalize(strip_state.region or Rect(0.05, 0.75, 0.9, 0.2), bounds)
        if region.w < 280:  # узкая область: строку всё равно делаем читаемой ширины
            cx = region.x + region.w / 2
            nw = min(w - 16, 420.0)
            region = Rect(max(8.0, min(cx - nw / 2, w - 8 - nw)), region.y, nw, region.h)
        px = font_px_for(h, 19.0, self.font_scale)
        family = self.font_family or FONT_FAMILY
        font = QFont(family)
        font.setWeight(QFont.Weight.Bold if self.font_bold else QFont.Weight.Medium)
        small = QFont(family)
        custom = self.strip_custom
        if custom is not None:
            # своё место: ширина своя, текст переносится по ней
            cw = max(200.0, min(w - 16, custom.w * w))
            region = Rect(max(8.0, min(custom.x * w, w - 8 - cw)), region.y, cw, region.h)
        original = self._original_shown(strip_state)
        if strip_state.hidden:
            return self._hint_geometry(strip_state, region, px, font, small, w, h)

        def measure(size: float, f: QFont, value: str) -> tuple[QRectF, QFontMetricsF]:
            f.setPixelSize(max(1, round(size)))
            fm = QFontMetricsF(f)
            text_w = max(40.0, region.w - 2 * size * 0.9)
            return fm.boundingRect(QRectF(0, 0, text_w, 100000), Qt.TextFlag.TextWordWrap, value), fm

        br, fm = measure(px, font, text)
        if br.height() > fm.lineSpacing() * 3.2:  # больше трёх строк: мельче, но не меньше 14
            px = max(14.0, px * 0.8)
            br, fm = measure(px, font, text)
        pad_x, pad_y = px * 0.9, px * 0.6
        orig_h = 0.0
        orig_br = None
        if original:
            small.setPixelSize(max(1, round(max(12.0, px * 0.78))))  # оригинал мельче перевода
            orig_br = QFontMetricsF(small).boundingRect(
                QRectF(0, 0, max(40.0, region.w - 2 * pad_x), 100000), Qt.TextFlag.TextWordWrap, original
            )
            orig_h = orig_br.height() + px * 0.35
        recall = None
        recall_h = 0.0
        if strip_state.recall:
            recall = self._layout_recall(strip_state.recall, region.w - 2 * pad_x, px, family)
            recall_h = recall.height + px * 0.45
        strip_h = br.height() + orig_h + recall_h + 2 * pad_y
        strip = self._place(region, strip_h, px, w, h)
        box = QRectF(strip.x, strip.y, strip.w, strip.h)
        orig_rect = None
        top = strip.y + pad_y
        if orig_br is not None:
            orig_rect = QRectF(strip.x + pad_x, top, strip.w - 2 * pad_x, orig_br.height())
            top += orig_h
        text_rect = QRectF(strip.x + pad_x, top, strip.w - 2 * pad_x, br.height())
        if recall is not None:  # раскладка была от нуля: сдвигаем под перевод
            dx, dy = strip.x + pad_x, text_rect.bottom() + px * 0.45
            recall = _RecallRow(
                recall.label.translated(dx, dy) if recall.label is not None else None,
                tuple(_ChipBox(c.rect.translated(dx, dy), c.word, c.translation, c.count) for c in recall.chips),
                recall.font,
                recall.bold,
                recall.height,
            )
        return box, font, text_rect, small, orig_rect, recall

    def _place(self, region: Rect, strip_h: float, px: float, w: float, h: float) -> Rect:
        """Где плашка высотой strip_h: своё место игры, вверху экрана, под или над окном диалога."""
        gap = max(6.0, px * 0.4)
        custom = self.strip_custom
        if custom is not None:
            y = max(4.0, min(custom.y * h, h - strip_h - 4))
            return Rect(region.x, y, region.w, strip_h)
        if self.strip_place == "top":
            return Rect(region.x, max(8.0, h * 0.03), region.w, strip_h)
        if self.strip_place == "below" and region.bottom + gap + strip_h <= h:
            return Rect(region.x, region.bottom + gap, region.w, strip_h)
        return place_strip(region, strip_h, Rect(0, 0, w, h), gap=gap)

    def _hint_geometry(
        self, strip_state: _Strip, region: Rect, px: float, font: QFont, small: QFont, w: float, h: float
    ) -> tuple[QRectF, QFont, QRectF, QFont, QRectF | None, _RecallRow | None]:
        """Учёба: перевод спрятан. Вместо плашки маленькая «Alt · перевод» на её месте, по центру."""
        size = max(12.0, px * 0.74)
        font.setPixelSize(round(size))
        font.setWeight(QFont.Weight.Medium)
        fm = QFontMetricsF(font)
        busy = strip_state.text is None
        text_w = size * 2.6 if busy else fm.horizontalAdvance(STUDY_HINT)
        pad_x, pad_y = size * 0.9, size * 0.42
        bw, bh = text_w + 2 * pad_x, fm.height() + 2 * pad_y
        strip = self._place(region, bh, px, w, h)
        x = max(4.0, min(region.x + (region.w - bw) / 2, w - bw - 4))
        box = QRectF(x, strip.y, bw, bh)
        text_rect = QRectF(x + pad_x, strip.y + pad_y, text_w, fm.height())
        return box, font, text_rect, small, None, None

    @staticmethod
    def _layout_recall(chips: tuple[RecallChip, ...], width: float, px: float, family: str) -> _RecallRow:
        """Подсказки «слово · перевод» в ряд (переносятся, если не влезают), от точки (0, 0)."""
        size = max(12.0, px * 0.72)
        f = QFont(family)
        f.setPixelSize(round(size))
        fb = QFont(f)
        fb.setWeight(QFont.Weight.DemiBold)
        fm, fmb = QFontMetricsF(f), QFontMetricsF(fb)
        h = fm.height() + size * 0.42
        pad, gap = size * 0.62, size * 0.5

        def chip_w(c: RecallChip, tr: str, count: str) -> float:
            w = pad * 2 + fmb.horizontalAdvance(c.word)
            if tr:
                w += fm.horizontalAdvance(" · " + tr)
            if count:
                w += fm.horizontalAdvance("  " + count)
            return w

        boxes: list[tuple[RecallChip, str, str, float]] = []
        for c in chips:
            count = f"×{c.seen}" if c.seen >= 2 else ""
            tr = c.translation
            w = chip_w(c, tr, count)
            if w > width and tr:  # длинный перевод: обрезаем его, слово оставляем целым
                tr = fm.elidedText(tr, Qt.TextElideMode.ElideRight, max(0.0, fm.horizontalAdvance(tr) - (w - width)))
                w = chip_w(c, tr, count)
            boxes.append((c, tr, count, min(w, width)))
        label_w = fm.horizontalAdvance(RECALL_LABEL)
        label = None
        x = 0.0
        if boxes and label_w + gap + boxes[0][3] <= width:
            label = QRectF(0, 0, label_w, h)
            x = label_w + gap
        y = 0.0
        out: list[_ChipBox] = []
        for c, tr, count, w in boxes:
            if out and x + w > width:
                x, y = 0.0, y + h + gap * 0.6
            out.append(_ChipBox(QRectF(x, y, w, h), c.word, tr, count))
            x += w + gap
        return _RecallRow(label, tuple(out), f, fb, y + h)

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        if self._blank:
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            p.fillRect(self.rect(), Qt.GlobalColor.transparent)
            p.end()
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        if self._page and not self._muted and not self._peek:
            self._paint_page(p)
        if self._glosses and not self._muted and not self._page:
            self._paint_glosses(p)
        strip = self._shown() if self.strip_visible else self._ghost
        if strip is not None and self._alpha > 0.0 and not self._muted:
            p.setOpacity(max(0.0, min(1.0, self._alpha)))
            self._paint_strip(p, strip, live=self.strip_visible)
            p.setOpacity(1.0)
        if self._toast:
            self._paint_toast(p, self._toast)
        p.end()

    def _paint_strip(self, p: QPainter, strip: _Strip, *, live: bool = True) -> None:
        box, font, text_rect, small, orig_rect, recall = self._strip_geometry(strip)
        if live:
            box = self._box(box)
        bg = QColor(STRIP_BG)
        bg.setAlphaF(max(0.3, min(1.0, self.opacity)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        radius = min(12.0, box.height() / 2)
        p.drawRoundedRect(box, radius, radius)
        p.setClipRect(box)  # пока плашка растёт, новая строка проявляется вместе с ней
        if strip.hidden:
            if strip.text is None:
                self._paint_dots(p, text_rect, font)
            else:
                p.setFont(font)
                p.setPen(TEXT_MUTED)
                p.drawText(text_rect, int(Qt.AlignmentFlag.AlignCenter), STUDY_HINT)
            p.setClipping(False)
            return
        original = self._original_shown(strip)
        if orig_rect is not None and original:
            p.setFont(small)
            p.setPen(TEXT_MUTED)
            p.drawText(orig_rect, int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft), original)
        if recall is not None:
            self._paint_recall(p, recall)
        if strip.text is None:
            self._paint_dots(p, text_rect, font)
            p.setClipping(False)
            return
        p.setFont(font)
        p.setPen(TEXT)
        shown = strip.text[: self._reveal] if live and self._reveal < len(strip.text) else strip.text
        p.drawText(text_rect, int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft), shown)
        p.setClipping(False)

    @staticmethod
    def _paint_recall(p: QPainter, row: _RecallRow) -> None:
        """Сохранённые слова, которые снова встретились: жёлтые, как твои слова в режиме слов."""
        left = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if row.label is not None:
            p.setFont(row.font)
            p.setPen(TEXT_MUTED)
            p.drawText(row.label, left, RECALL_LABEL)
        fm, fmb = QFontMetricsF(row.font), QFontMetricsF(row.bold)
        for c in row.chips:
            r = c.rect
            fill = QColor(KNOWN)
            fill.setAlphaF(0.13)
            line = QColor(KNOWN)
            line.setAlphaF(0.5)
            p.setPen(QPen(line, 1.0))
            p.setBrush(fill)
            p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), r.height() / 2, r.height() / 2)
            pad = row.font.pixelSize() * 0.62
            x = r.x() + pad
            p.setFont(row.bold)
            p.setPen(KNOWN)
            p.drawText(QRectF(x, r.y(), r.right() - x, r.height()), left, c.word)
            x += fmb.horizontalAdvance(c.word)
            p.setFont(row.font)
            if c.translation:
                part = " · " + c.translation
                p.setPen(QColor("#ECE9E0"))
                p.drawText(QRectF(x, r.y(), max(0.0, r.right() - x), r.height()), left, part)
                x += fm.horizontalAdvance(part)
            if c.count:
                p.setPen(TEXT_MUTED)
                p.drawText(QRectF(x, r.y(), max(0.0, r.right() - x), r.height()), left, "  " + c.count)

    def _paint_glosses(self, p: QPainter) -> None:
        """Значения над словами: светлые (новое слово), «?» бирюзовый (вспомни сам), «?» жёлтый (твоё слово)."""
        bg = QColor(STRIP_BG)
        bg.setAlphaF(0.86)
        for i, box, word, font in self.gloss_boxes():
            g = self._glosses[i]
            color = QColor(KNOWN) if g.kind == "saved" else QColor(ACCENT)
            line = QColor(color)
            line.setAlphaF(0.85 if g.kind == "new" or g.open else 0.6)
            pen = QPen(line, max(1.5, font.pixelSize() * 0.1))
            if g.kind != "new" and not g.open:
                pen.setStyle(Qt.PenStyle.DotLine)
            p.setPen(pen)
            y = word.bottom() + 1.5
            p.drawLine(word.left() + 1, y, word.right() - 1, y)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            radius = box.height() / 2
            p.drawRoundedRect(box, radius, radius)
            p.setFont(font)
            text = g.shown
            if text == "?":
                p.setPen(color)
            elif g.kind == "saved":
                p.setPen(QColor(KNOWN))
            else:
                p.setPen(QColor("#ECE9E0"))
            p.drawText(box, int(Qt.AlignmentFlag.AlignCenter), text)

    def _paint_dots(self, p: QPainter, text_rect: QRectF, font: QFont) -> None:
        """Идёт перевод: три точки, яркая бежит слева направо."""
        size = max(5.0, font.pixelSize() * 0.42)
        gap = size * 0.75
        cy = text_rect.center().y()
        x = text_rect.x() + size * 0.2
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(3):
            c = QColor(TEXT if i == self._dots else TEXT_MUTED)
            if i != self._dots:
                c.setAlphaF(0.55)
            p.setBrush(c)
            p.drawEllipse(QRectF(x + i * (size + gap), cy - size / 2, size, size))

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
