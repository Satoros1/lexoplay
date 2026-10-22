"""Оверлей поверх игры: строка перевода над окном диалога и тосты в углу.

Одно прозрачное окно размером с display_rect, клики проходят насквозь, фокус не берёт.
Ставится в физических координатах через `placer` (на Windows это SetWindowPos), а рисует
всё в логических пикселях относительно своего размера, поэтому DPI и Magpie не мешают.

Плашка появляется и исчезает плавно (доли секунды), а пока идёт перевод, в ней бегут
три точки. Логически она скрыта сразу: исчезающая копия только дорисовывается.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PySide6.QtCore import QEasingCurve, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from lexoplay.geometry import Rect, denormalize, font_px_for, place_strip
from lexoplay.recall import RecallChip
from lexoplay.ui.theme import ACCENT, FONT_FAMILY, KNOWN, STRIP_BG, TEXT, TEXT_MUTED

Placer = Callable[[QWidget, Rect], None]

FADE_IN_MS = 120
FADE_OUT_MS = 140
DOTS_MS = 300  # шаг бегущих точек, пока идёт перевод


@dataclass(frozen=True, slots=True)
class _Strip:
    """Что нарисовано в плашке: нужно, чтобы дорисовать её, пока она исчезает."""

    region: Rect | None
    text: str | None  # None: идёт перевод (точки)
    original: str | None
    recall: tuple[RecallChip, ...] = ()  # сохранённые слова из этой реплики: строка подсказок под переводом


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
        self._dots_timer = QTimer(self)
        self._dots_timer.setInterval(DOTS_MS)
        self._dots_timer.timeout.connect(self._tick_dots)
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

    @property
    def recall(self) -> tuple[RecallChip, ...]:
        return self._recall

    def show_busy(self, region: Rect, original: str | None = None, recall: Sequence[RecallChip] = ()) -> None:
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
        self._region, self._text, self._busy = region, text, False
        self._original = original if original and original.strip() else None
        self._recall = tuple(recall)
        self._dots_timer.stop()
        self._appear(appearing)
        self._refresh()

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
            self._ghost = _Strip(self._region, self._text, self._original, self._recall)
            self._fade_to(0.0, FADE_OUT_MS)
        self._text, self._busy, self._original, self._recall = None, False, None, ()
        self._dots_timer.stop()
        self._refresh()

    # --- плавность ---
    def _appear(self, appearing: bool) -> None:
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
        if not self._busy or self._text is not None:
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
        return _Strip(self._region, self._text, self._original, self._recall)

    def toast(self, text: str, ms: int = 2400) -> None:
        self._toast = text
        self._toast_timer.start(ms)
        self._refresh()

    def _clear_toast(self) -> None:
        self._toast = None
        self._refresh()

    def _refresh(self) -> None:
        need = (not self._muted and (self.strip_visible or self._ghost is not None)) or self._toast is not None
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
        gap = max(6.0, px * 0.4)
        if custom is not None:
            y = max(4.0, min(custom.y * h, h - strip_h - 4))
            strip = Rect(region.x, y, region.w, strip_h)
        elif self.strip_place == "top":
            strip = Rect(region.x, max(8.0, h * 0.03), region.w, strip_h)
        elif self.strip_place == "below" and region.bottom + gap + strip_h <= h:
            strip = Rect(region.x, region.bottom + gap, region.w, strip_h)
        else:
            strip = place_strip(region, strip_h, bounds, gap=gap)
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
        strip = self._shown() if self.strip_visible else self._ghost
        if strip is not None and self._alpha > 0.0 and not self._muted:
            p.setOpacity(max(0.0, min(1.0, self._alpha)))
            self._paint_strip(p, strip)
            p.setOpacity(1.0)
        if self._toast:
            self._paint_toast(p, self._toast)
        p.end()

    def _paint_strip(self, p: QPainter, strip: _Strip) -> None:
        box, font, text_rect, small, orig_rect, recall = self._strip_geometry(strip)
        bg = QColor(STRIP_BG)
        bg.setAlphaF(max(0.3, min(1.0, self.opacity)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        radius = min(12.0, box.height() / 2)
        p.drawRoundedRect(box, radius, radius)
        original = self._original_shown(strip)
        if orig_rect is not None and original:
            p.setFont(small)
            p.setPen(TEXT_MUTED)
            p.drawText(orig_rect, int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft), original)
        if recall is not None:
            self._paint_recall(p, recall)
        if strip.text is None:
            self._paint_dots(p, text_rect, font)
            return
        p.setFont(font)
        p.setPen(TEXT)
        p.drawText(text_rect, int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft), strip.text)

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
