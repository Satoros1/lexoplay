"""Режим слов (F3) без Qt: какие слова кликабельны, выбор фразы, где поставить карточку.

Рамки слов нормализованы (0..1 от окна игры), поэтому одинаково ложатся на окно игры,
полноэкранный режим и картинку под Magpie. В панели режима слов рамки ставит сама панель
(reader_views), у слов из OCR (build_views) они с экрана.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from typing import Literal

from lexoplay.geometry import Rect
from lexoplay.ocr_model import OcrResult
from lexoplay.rarity import rank
from lexoplay.rarity import tier as rarity_tier
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
    tier: str = ""  # 'rare' трудное, 'common' из самых частых, 'name' имя; '' обычное (или подсветка выключена)


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


_SENTENCE_END = re.compile(r"[.!?…:;\"“”«»(\[-]$")


def _name_like(tokens: list[str], i: int, caps_line: bool) -> bool:
    """С большой буквы посреди предложения: скорее всего имя или название, а не трудное слово."""
    core = clean(tokens[i])
    if caps_line or not core or not core[0].isupper() or core.isupper() or core in ("I", "I'm", "I'll", "I've", "I'd"):
        return False
    if i == 0:
        return False
    prev = tokens[i - 1].rstrip("'’")
    return not _SENTENCE_END.search(prev)


def _caps_line(tokens: list[str]) -> bool:
    letters = [ch for t in tokens for ch in t if ch.isalpha()]
    return bool(letters) and sum(ch.isupper() for ch in letters) > len(letters) * 0.6


def game_names(texts: Collection[str], speakers: Collection[str] = (), terms: Collection[str] = ()) -> set[str]:
    """Имена и названия игры: говорящие и слова из глоссария (не из словаря), и то, что в репликах посреди предложения
    пишется с большой буквы и ни разу не встретилось с маленькой (Hope's Peak да, Hope в «hope» нет)."""
    upper: set[str] = set()
    lower: set[str] = set()
    for text in texts:
        tokens = text.split()
        caps = _caps_line(tokens)
        for i, token in enumerate(tokens):
            core = clean(token).replace("’", "'").split("'")[0]
            if not core or not core.isalpha():
                continue
            if core.islower():
                lower.add(core)
            elif not caps and _name_like(tokens, i, caps):
                upper.add(core)
    out = {w for w in upper if w.casefold() not in lower}
    for value in (*speakers, *terms):  # из составных берём только слова не из словаря: «Ultimate Lucky Student» не имя
        out.update(w for w in (clean(x) for x in value.split()) if len(w) > 1 and rank(w) is None)
    return out


def reader_views(
    text: str,
    known: Callable[[str], bool] = lambda _w: False,
    *,
    level: str | None = None,
    names: Collection[str] = (),
) -> list[WordView]:
    """Слова реплики для панели режима слов. Рамки им ставит сама панель, когда раскладывает текст.

    level: с какого уровня подсвечивать трудные слова (rarity.LEVELS), None: не подсвечивать.
    names: имена и названия игры (говорящий, глоссарий, что в репликах пишется с большой буквы):
    они не «трудные», даже если их нет в словаре.
    """
    tokens = text.split()
    caps = _caps_line(tokens)
    lowered = {n.casefold() for n in names}
    out: list[WordView] = []
    for i, token in enumerate(tokens):
        word = is_word(token)
        stop = word and is_stopword(token)
        tier = ""
        if word and not stop and level:
            core = clean(token).replace("’", "'")
            base = core.split("'")[0]
            if core.casefold() in lowered or base.casefold() in lowered or _name_like(tokens, i, caps):
                tier = "name"
            else:
                tier = rarity_tier(token, level)
        out.append(
            WordView(
                index=i,
                text=token,
                rect=Rect(0, 0, 0, 0),
                known=bool(word and not stop and known(token)),
                stop=stop,
                clickable=word,
                tier=tier,
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


# ---------------------------------------------------------------------- выбор стрелками
def word_lines(views: list[WordView]) -> list[list[WordView]]:
    """Слова по строкам: соседние по порядку, если середины по высоте близко."""
    rows: list[list[WordView]] = []
    for v in views:
        cy, h = v.rect.y + v.rect.h / 2, v.rect.h
        if rows:
            last = rows[-1][-1]
            if abs(cy - (last.rect.y + last.rect.h / 2)) <= max(h, last.rect.h) * 0.6:
                rows[-1].append(v)
                continue
        rows.append([v])
    return rows


def next_word(views: list[WordView], current: int | None, step: int) -> int | None:
    """Соседнее слово по порядку чтения (step +1 вправо, -1 влево); у края остаёмся на месте."""
    words = [v.index for v in views if v.clickable]
    if not words:
        return None
    if current is None:
        return words[0] if step > 0 else words[-1]
    ahead = [i for i in words if (i > current if step > 0 else i < current)]
    if not ahead:
        return current
    return ahead[0] if step > 0 else ahead[-1]


def word_in_line(views: list[WordView], current: int | None, step: int) -> int | None:
    """Слово в строке выше (step -1) или ниже (+1), ближайшее по горизонтали."""
    if current is None:
        return next_word(views, None, step)
    rows = [[v for v in row if v.clickable] for row in word_lines(views)]
    rows = [r for r in rows if r]
    at = next((i for i, r in enumerate(rows) if any(v.index == current for v in r)), None)
    if at is None or not 0 <= at + step < len(rows):
        return current
    cur = next(v for v in rows[at] if v.index == current)
    cx = cur.rect.x + cur.rect.w / 2
    return min(rows[at + step], key=lambda v: abs(v.rect.x + v.rect.w / 2 - cx)).index


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
    local: bool = False  # без Lexo: слово сохраняется в список в LexoPlay

    @property
    def subtitle(self) -> str:
        parts = [f"/{self.ipa}/" if self.ipa else "", self.pos]
        return " · ".join(p for p in parts if p)

    @property
    def button(self) -> str | None:
        """Подпись кнопки или None, если кнопки нет."""
        if self.phase == "saved":
            return None
        if self.local:
            return "+ в мои слова"
        if self.phase == "ready" and self.in_deck:
            return "+ пример из игры"
        return "+ в Lexo"

    @property
    def button_enabled(self) -> bool:
        return self.phase in ("ready", "offline", "error")


# ---------------------------------------------------------------------- разбор реплики
@dataclass
class ExplainRow:
    phrase: str
    meaning: str
    note: str = ""
    indices: list[int] = field(default_factory=list)  # слова реплики, к которым относится
    saved: bool = False
    base: str = ""  # словарная форма, если отличается от фразы

    @property
    def lemma(self) -> str:
        return self.base or self.phrase


@dataclass
class ExplainState:
    phase: Literal["loading", "ready", "error"]
    rows: list[ExplainRow] = field(default_factory=list)
    summary: str = ""  # смысл реплики целиком
    note: str = ""  # текст ошибки
    literal: str = ""  # дословно


def _norm_token(text: str) -> str:
    return "".join(ch for ch in text.lower().replace("’", "'") if ch.isalnum() or ch == "'").strip("'")


def match_phrase(views: list[WordView], phrase: str) -> list[int]:
    """Индексы слов реплики, из которых состоит фраза (первое совпадение подряд)."""
    want = [t for t in (_norm_token(w) for w in phrase.split()) if t]
    if not want:
        return []
    have = [_norm_token(v.text) for v in views]
    n = len(want)
    for i in range(len(have) - n + 1):
        if have[i : i + n] == want:
            return [views[j].index for j in range(i, i + n)]
    # OCR склеил или разбил слова иначе: ищем хотя бы самое длинное слово фразы
    longest = max(want, key=len)
    for i, t in enumerate(have):
        if t == longest and len(t) >= 4:
            return [views[i].index]
    return []
