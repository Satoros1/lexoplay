"""Сохранённые слова, которые снова встретились: под переводом реплики мелкая подсказка «eerie · жуткий».

Так сохранённое слово повторяется прямо в игре, в новом контексте. Подсказка есть всегда, когда
сохранённое слово есть в реплике (до трёх слов за реплику): подчёркнуто жёлтым в F3, значит и под
переводом видно. Встречей считается только новая реплика: не та, из которой слово сохраняли, не та
же реплика ещё раз и не первые минуты после сохранения. «Встречалось N раз» видно во вкладке «Слова»
и в подсказке (×N).
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from lexoplay.dedupe import normalize_text
from lexoplay.rarity import bases
from lexoplay.store import SavedWord, fold
from lexoplay.words import forms, is_stopword

FRESH_S = 120.0  # только что сохранил: напоминать рано
MAX_CHIPS = 3
TRANSLATION_CHARS = 32

_TOKEN = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
# заполнители в словарной форме выражения: give sb a hand, make up one's mind
_SOMEONE = frozenset("sb smb sth smth someone somebody something".split())  # 1-3 любых слова
_ONES = frozenset("one's someone's somebody's".split())  # my, your, his, Makoto's...
_PLACEHOLDERS = _SOMEONE | _ONES
_POSSESSIVE = frozenset("my your his her its our their".split())
# отделяемая частица фразового глагола: pick it up, figure the whole thing out
_PARTICLES = frozenset("up out off down in on away back over around through along apart together".split())
_OBJECT_START = frozenset(
    "it them him her me us you this that these those everything something someone everyone the a an his my your their our its".split()
)
MAX_GAP = 3


def tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(text.replace("’", "'").replace("‘", "'"))]


def variants(token: str) -> set[str]:
    """Словарные формы слова из реплики. Те же, по которым F3 подчёркивает слово жёлтым
    (words.forms: stuck -> stick, eerily -> eerie, happier -> happy), и сокращения (she'd -> she)."""
    return {token} | bases(token) | forms(token)


def _match(parts: tuple[str, ...], toks: list[str], vars_: list[set[str]], i: int) -> int | None:
    """Выражение parts с позиции i реплики: конец найденного (не включая) или None."""

    def go(k: int, j: int) -> int | None:
        if k == len(parts):
            return j
        p = parts[k]
        if p in _SOMEONE:
            for n in range(1, 4):
                if j + n <= len(toks) and (end := go(k + 1, j + n)) is not None:
                    return end
            return None
        if p in _ONES:
            if j < len(toks) and (toks[j] in _POSSESSIVE or toks[j].endswith("'s")):
                return go(k + 1, j + 1)
            return None
        if j < len(toks) and p in vars_[j] and (end := go(k + 1, j + 1)) is not None:
            return end
        if k > 0 and p in _PARTICLES and j < len(toks) and toks[j] in _OBJECT_START:
            for gap in range(1, MAX_GAP + 1):  # pick it up: частица после дополнения
                if j + gap < len(toks) and p in vars_[j + gap] and (end := go(k + 1, j + gap + 1)) is not None:
                    return end
        return None

    return go(0, i)


def word_spans(sentence: str, selection: str, lemma: str | None = None) -> list[tuple[int, int]]:
    """Где в примере само сохранённое слово (для жирного во вкладке «Слова»): (начало, конец).

    Сначала ищем то, что выделяли в игре, как есть, потом словарную форму по словоформам
    (сохранил lure, в примере lured out)."""
    text = sentence.replace("\u2019", "'").replace("\u2018", "'")
    sel = selection.strip()
    toks = list(_TOKEN.finditer(text))
    forms = [variants(t.group(0).lower()) for t in toks]

    def by_forms(target: str | None) -> list[tuple[int, int]]:
        parts = tokens(target or "")
        for i in range(len(toks) - len(parts) + 1) if parts else ():
            if all(parts[k] in forms[i + k] for k in range(len(parts))):
                return [(toks[i].start(), toks[i + len(parts) - 1].end())]
        return []

    if lemma and len(tokens(lemma)) > 1 and (found := by_forms(lemma)):
        return found  # выражение целиком: jump to conclusions, а не только jump to
    if sel:  # без регулярки: в списке слов это сотни разных шаблонов
        low, needle = text.lower(), sel.replace("\u2019", "'").lower()
        i = low.find(needle)
        while i >= 0:
            j = i + len(needle)
            if (i == 0 or not low[i - 1].isalpha()) and (j >= len(low) or not low[j].isalpha()):
                return [(i, j)]
            i = low.find(needle, i + 1)
    return by_forms(lemma) or by_forms(sel)


def short_translation(text: str) -> str:
    """Первое значение: «жуткий, зловещий; странный» -> «жуткий»."""
    first = re.split(r"[;,/(\n]| - | – ", text, maxsplit=1)[0].strip(" .\t")
    if len(first) > TRANSLATION_CHARS:
        first = first[: TRANSLATION_CHARS - 1].rstrip() + "…"
    return first


@dataclass(frozen=True, slots=True)
class RecallChip:
    word: str
    translation: str
    seen: int  # сколько раз встретилось после сохранения, вместе с этим


@dataclass(slots=True)
class _Entry:
    key: str
    word: str
    translation: str
    parts: tuple[str, ...]
    ids: list[int] = field(default_factory=list)
    sentences: set[str] = field(default_factory=set)
    created_at: float = 0.0
    seen: int = 0


def _parts(word: str) -> tuple[str, ...] | None:
    """Слово или выражение для поиска; None: не напоминаем (служебное, короткое, начинается с заполнителя)."""
    parts = tuple(tokens(word))
    if not parts or parts[0] in _PLACEHOLDERS or all(p in _PLACEHOLDERS for p in parts):
        return None
    if len(parts) == 1 and (len(parts[0]) < 3 or is_stopword(parts[0])):
        return None
    return parts


class RecallIndex:
    """Сохранённые слова, разложенные для быстрого поиска в реплике (и по словоформам: ran -> run)."""

    def __init__(self, words: Iterable[SavedWord], lexo: Mapping[str, str] | None = None) -> None:
        """words: сохранённые в LexoPlay; lexo: слова из словаря Lexo (бота) с переводом."""
        by_key: dict[str, _Entry] = {}
        for w in words:  # words_list отдаёт новые первыми: перевод берём у последнего сохранения
            if not w.translation or not w.translation.strip():
                continue
            parts = _parts(w.word)
            if parts is None:
                continue
            key = fold(" ".join(parts))
            e = by_key.get(key)
            if e is None:
                e = by_key[key] = _Entry(key, w.word.strip(), short_translation(w.translation), parts)
            e.ids.append(w.id)
            if w.sentence:
                e.sentences.add(normalize_text(w.sentence))
            e.created_at = max(e.created_at, w.created_at)
            e.seen = max(e.seen, w.seen)
        for word, tr in (lexo or {}).items():  # из Lexo: встречи не считаем (их там нет)
            parts = _parts(word)
            if parts is None or not tr or not tr.strip():
                continue
            key = fold(" ".join(parts))
            if key not in by_key:
                by_key[key] = _Entry(key, word.strip(), short_translation(tr), parts)
        self.entries = list(by_key.values())
        # ищем от слова в реплике к словарной форме (gave up -> give up), а не наоборот: иначе
        # сохранённое seed нашлось бы в see
        self._first: dict[str, list[_Entry]] = {}
        for e in self.entries:
            self._first.setdefault(e.parts[0], []).append(e)

    def __len__(self) -> int:
        return len(self.entries)

    def find(self, text: str) -> list[_Entry]:
        """Какие сохранённые слова есть в реплике, по порядку. Выражение важнее слова внутри него."""
        toks = tokens(text)
        vars_ = [variants(t) for t in toks]
        found: list[tuple[int, _Entry]] = []
        taken: set[int] = set()  # слова, вошедшие в найденное выражение
        seen: set[str] = set()
        for i in range(len(toks)):
            cands = {id(e): e for f in vars_[i] for e in self._first.get(f, ())}
            for e in sorted(cands.values(), key=lambda x: -len(x.parts)):
                n = len(e.parts)
                if e.key in seen or (n == 1 and i in taken):
                    continue
                end = _match(e.parts, toks, vars_, i) if n > 1 else (i + 1 if e.parts[0] in vars_[i] else None)
                if end is not None:
                    seen.add(e.key)
                    found.append((i, e))
                    if n > 1:
                        taken.update(range(i, end))
        return [e for _i, e in sorted(found, key=lambda x: x[0])]


class Recaller:
    """Решает, что из найденного показать сейчас, и считает встречи."""

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._counted: dict[str, str] = {}  # слово -> реплика, где посчитали встречу последний раз

    def pick(self, index: RecallIndex, text: str, *, commit: bool = True) -> tuple[list[RecallChip], list[int]]:
        """(подсказки, id слов, которым засчитать встречу). commit=False: только посмотреть, не запоминая."""
        norm = normalize_text(text)
        now = self._clock()
        chips: list[RecallChip] = []
        counted: list[int] = []
        for e in index.find(text):
            seen = e.seen
            own_line = norm in e.sentences or now - e.created_at < FRESH_S  # там и сохраняли: не встреча
            if e.ids and not own_line and self._counted.get(e.key) != norm:  # слова из Lexo не считаем: их нет в «Словах»
                seen += 1
                counted.extend(e.ids)
                if commit:
                    self._counted[e.key] = norm
                    e.seen = seen
            if len(chips) < MAX_CHIPS:
                chips.append(RecallChip(e.word, e.translation, seen))
        return chips, counted
