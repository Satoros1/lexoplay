"""Сохранённые слова, которые снова встретились: под переводом реплики мелкая подсказка «eerie · жуткий».

Так сохранённое слово повторяется прямо в игре, в новом контексте. Чтобы подсказка не надоедала:
- не больше трёх слов за реплику;
- одно и то же слово не чаще раза в 15 минут (та же реплика, нажатая ещё раз, показывает его снова);
- не в той реплике, из которой слово сохраняли, и не в первые минуты после сохранения.
Каждая новая встреча считается: «встречалось N раз» видно во вкладке «Слова» и в подсказке (×N).
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from lexoplay.dedupe import normalize_text
from lexoplay.rarity import bases
from lexoplay.store import SavedWord, fold
from lexoplay.words import is_stopword

SHOW_EVERY_S = 15 * 60
FRESH_S = 120.0  # только что сохранил: напоминать рано
MAX_CHIPS = 3
TRANSLATION_CHARS = 32

_TOKEN = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_PLACEHOLDERS = frozenset("sb sth smb smth someone somebody something one's someone's".split())


def tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(text.replace("’", "'").replace("‘", "'"))]


def variants(token: str) -> set[str]:
    return {token} | bases(token)


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


class RecallIndex:
    """Сохранённые слова, разложенные для быстрого поиска в реплике (и по словоформам: ran -> run)."""

    def __init__(self, words: Iterable[SavedWord]) -> None:
        by_key: dict[str, _Entry] = {}
        for w in words:  # words_list отдаёт новые первыми: перевод берём у последнего сохранения
            if not w.translation or not w.translation.strip():
                continue
            parts = tuple(tokens(w.word))
            if not parts or any(p in _PLACEHOLDERS for p in parts):
                continue
            if len(parts) == 1 and (len(parts[0]) < 3 or is_stopword(parts[0])):
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
        forms = [variants(t) for t in toks]
        found: list[tuple[int, _Entry]] = []
        taken: set[int] = set()  # слова, вошедшие в найденное выражение
        seen: set[str] = set()
        for i in range(len(toks)):
            cands = {id(e): e for f in forms[i] for e in self._first.get(f, ())}
            for e in sorted(cands.values(), key=lambda x: -len(x.parts)):
                n = len(e.parts)
                if e.key in seen or i + n > len(toks) or (n == 1 and i in taken):
                    continue
                if all(e.parts[k] in forms[i + k] for k in range(1, n)):
                    seen.add(e.key)
                    found.append((i, e))
                    if n > 1:
                        taken.update(range(i, i + n))
        return [e for _i, e in sorted(found, key=lambda x: x[0])]


class Recaller:
    """Решает, что из найденного показать сейчас, и считает встречи."""

    def __init__(self, *, every_s: float = SHOW_EVERY_S, clock: Callable[[], float] = time.time) -> None:
        self.every_s = every_s
        self._clock = clock
        self._shown: dict[str, tuple[float, str]] = {}  # слово -> (когда показали, в какой реплике)
        self._counted: dict[str, str] = {}  # слово -> реплика, где посчитали встречу последний раз

    def pick(self, index: RecallIndex, text: str, *, commit: bool = True) -> tuple[list[RecallChip], list[int]]:
        """(подсказки, id слов, которым засчитать встречу). commit=False: только посмотреть, не запоминая."""
        norm = normalize_text(text)
        now = self._clock()
        chips: list[RecallChip] = []
        counted: list[int] = []
        for e in index.find(text):
            if norm in e.sentences or now - e.created_at < FRESH_S:
                continue
            new = self._counted.get(e.key) != norm
            seen = e.seen + (1 if new else 0)
            if new:
                counted.extend(e.ids)
                if commit:
                    self._counted[e.key] = norm
                    e.seen = seen
            shown = self._shown.get(e.key)
            if shown and shown[1] != norm and now - shown[0] < self.every_s:
                continue
            if len(chips) >= MAX_CHIPS:
                continue
            chips.append(RecallChip(e.word, e.translation, seen))
            if commit and (shown is None or shown[1] != norm):
                self._shown[e.key] = (now, norm)
        return chips, counted
