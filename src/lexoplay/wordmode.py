"""Режим слов (F3) без Qt: какие слова кликабельны, выбор фразы, где поставить карточку.

Рамки слов из OCR нормализованы (0..1 от области игры), поэтому одинаково ложатся
на окно игры, полноэкранный режим и картинку под Magpie.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from lexoplay.geometry import Rect
from lexoplay.ocr_model import OcrResult
from lexoplay.words import clean, is_stopword, is_word

MAX_PHRASE = 5


@dataclass(frozen=True, slots=True)
class WordView:
    index: int
    text: str
    rect: Rect  # нормализованная
    known: bool = False  # уже есть в Lexo: жёлтое подчёркивание
    stop: bool = False  # служебное слово: не подчёркиваем, но кликнуть можно
    clickable: bool = True


def build_views(ocr: OcrResult, known: Callable[[str], bool] = lambda _w: False) -> list[WordView]:
    out: list[WordView] = []
    for i, w in enumerate(ocr.words):
        word = is_word(w.text)
        stop = word and is_stopword(w.text)
        out.append(
            WordView(
                index=i,
                text=w.text,
                rect=w.rect,
                known=bool(word and not stop and known(w.text)),
                stop=stop,
                clickable=word,
            )
        )
    return out


def selection_range(anchor: int | None, index: int, *, max_words: int = MAX_PHRASE) -> list[int]:
    """Shift+клик: от якоря до слова, не длиннее max_words, считая от якоря."""
    if anchor is None or anchor == index:
        return [index]
    step = 1 if index > anchor else -1
    end = anchor + step * (min(abs(index - anchor), max_words - 1))
    lo, hi = sorted((anchor, end))
    return list(range(lo, hi + 1))


def selected_text(views: list[WordView], indices: list[int]) -> str:
    tokens = [views[i].text for i in sorted(indices) if 0 <= i < len(views)]
    return clean(" ".join(tokens))


def union(rects: list[Rect]) -> Rect:
    if not rects:
        return Rect(0, 0, 0, 0)
    x1 = min(r.x for r in rects)
    y1 = min(r.y for r in rects)
    x2 = max(r.right for r in rects)
    y2 = max(r.bottom for r in rects)
    return Rect(x1, y1, x2 - x1, y2 - y1)


def place_card(
    sel: Rect, card_w: float, card_h: float, bounds: Rect, gap: float = 8.0, avoid: Rect | None = None
) -> Rect:
    """Где поставить карточку слова.

    avoid = окно диалога вместе со строкой перевода: их карточка не закрывает, чтобы
    реплика и перевод оставались видны. Ставим её над ними (диалог обычно внизу) или
    под ними (диалог вверху). Не влезает никуда → под словом или над ним.
    """
    margin = 8.0
    x = min(max(bounds.x + margin, sel.x), bounds.right - margin - card_w)
    x = max(bounds.x + margin, x)
    if avoid is not None:
        if avoid.y - gap - card_h >= bounds.y + margin:
            return Rect(x, avoid.y - gap - card_h, card_w, card_h)
        if avoid.bottom + gap + card_h <= bounds.bottom - margin:
            return Rect(x, avoid.bottom + gap, card_w, card_h)
    below = sel.bottom + gap
    above = sel.y - gap - card_h
    if below + card_h <= bounds.bottom - margin:
        y = below
    elif above >= bounds.y + margin:
        y = above
    else:
        y = max(bounds.y + margin, bounds.bottom - margin - card_h)
    return Rect(x, y, card_w, card_h)


Phase = Literal["loading", "ready", "offline", "saved", "error"]


@dataclass
class CardState:
    phase: Phase
    title: str
    ipa: str = ""
    pos: str = ""
    senses: list[str] = field(default_factory=list)
    in_deck: bool = False
    note: str = ""

    @property
    def subtitle(self) -> str:
        parts = [f"/{self.ipa}/" if self.ipa else "", self.pos]
        return " · ".join(p for p in parts if p)

    @property
    def button(self) -> str | None:
        """Подпись кнопки или None, если кнопки нет."""
        if self.phase == "saved":
            return None
        if self.phase == "ready" and self.in_deck:
            return "+ пример из игры"
        return "+ в Lexo"

    @property
    def button_enabled(self) -> bool:
        return self.phase in ("ready", "offline", "error")
