"""Учёба: реплику читаешь по-английски сам, над трудными словами маленькие подсказки, перевод спрятан.

Это добавка к обычному переводу (своя клавиша включает и выключает), а не урок: ни тестов, ни окон,
ни кликов. Перевод реплики на месте, его видно, пока держишь Alt или наводишь на плашку.
Что видно над словом, решают частота слова и то, сколько раз ты его уже встречал:

- новое трудное слово (реже уровня по частотному списку, не имя): значение видно сразу,
  первые SEEN_TO_TEST встреч;
- потом вместо значения «?»: вспомни сам, наведи курсор, если не вспомнил;
- «?» не открывал PASSES_TO_KNOWN раз (и перевод реплики не подглядывал): слово знаешь, подсказки
  больше нет. Открыл: значение снова видно ещё BACK_SEEN встреч;
- сохранённые слова (свои и из Lexo) всегда под «?»: их учишь в карточках, в игре только вспоминаешь.

Встреча считается, только если реплика была на экране хотя бы MIN_SHOW_S: пролистанную не читал.
Значения пишет та же модель, что переводит, в том же запросе (translate.GLOSS_MARK).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from lexoplay.dedupe import normalize_text
from lexoplay.geometry import Rect
from lexoplay.rarity import _junk, _ranks, bases, norm, rank
from lexoplay.recall import _TOKEN, RecallIndex
from lexoplay.store import StudyWord
from lexoplay.wordmode import _caps_line, _name_like
from lexoplay.words import clean, is_stopword, is_word

# с какого места в частотном списке слово трудное для уровня (B1 знает примерно 3000-3500 слов)
LEVELS: dict[str, int] = {"a2": 2000, "b1": 3500, "b2": 6000}
LEVEL_NAMES: dict[str, str] = {"a2": "A2", "b1": "B1", "b2": "B2"}
DEFAULT_LEVEL = "b1"
MAX_NEW = 3  # видимых значений на реплику: больше уже мешает читать
MAX_MARKS = 3  # «?» на реплику
SEEN_TO_TEST = 4
PASSES_TO_KNOWN = 2
BACK_SEEN = 2
MIN_SHOW_S = 1.5
SKIP = "-"  # модель: это имя, мусор OCR или слово, которое знает любой на этом уровне
_UNRANKED = 20000  # слова нет в списке: считаем самым редким

Kind = Literal["new", "test", "saved"]


@dataclass(frozen=True, slots=True)
class Pick:
    """Слово реплики, над которым будет подсказка."""

    index: int  # слово в ocr.words
    token: str  # как в реплике, без знаков по краям и без заикания (b-b-blabbermouth -> blabbermouth)
    key: str  # словарная форма, по ней учёт: despaired -> despair
    kind: Kind
    meaning: str = ""  # сохранённое слово: свой перевод (у модели не спрашиваем)
    label: str = ""  # сохранённое выражение целиком (beat around the bush), если это выражение

    @property
    def ask(self) -> bool:
        """Значение спросить у модели."""
        return not self.meaning


@dataclass(slots=True)
class Shown:
    """Реплика в учёбе, пока она на экране: что над какими словами и что игрок открывал."""

    exe: str
    text: str
    picks: tuple[Pick, ...]
    since: float  # time.monotonic(), когда показали
    rects: tuple[Rect, ...] = ()  # рамки слов OCR (Pick.index), в долях окна игры
    meanings: dict[int, str] = field(default_factory=dict)  # Pick.index -> значение (или SKIP)
    revealed: set[int] = field(default_factory=set)  # открытые наведением «?»
    peeked: bool = False  # подглядывал перевод реплики

    def meaning(self, p: Pick) -> str:
        return p.meaning or self.meanings.get(p.index, "")


_STUTTER = re.compile(r"^(?:([a-z]{1,2})-)+(?=([a-z]+))", re.IGNORECASE)


def destutter(token: str) -> str:
    """b-b-blabbermouth -> blabbermouth, W-wait -> wait: заикание не часть слова."""
    m = _STUTTER.match(token)
    if m and m.group(2).lower().startswith(m.group(1).lower()):
        return token[m.end() :]
    return token


def lemma(token: str) -> str:
    """Словарная форма для учёта: самая частая из форм слова (despaired -> despair, glasses -> glass)."""
    w = norm(token)
    ranks = _ranks()
    best, best_r = w, ranks.get(w)
    for b in sorted(bases(w)):
        r = ranks.get(b)
        if r is not None and (best_r is None or r < best_r):
            best, best_r = b, r
    return best


def _hard(w: str, threshold: int) -> bool:
    if "-" in w:  # half-baked: трудное, если трудная хоть одна часть
        return any(_hard(p, threshold) for p in w.split("-") if len(p) > 1)
    r = rank(w)
    if r is None:
        return len(w) >= 4 and "'" not in w and not _junk(w)  # y'know, ma'am: разговорное
    return r >= threshold


def _joined(words: Sequence[str]) -> tuple[str, list[int]]:
    """Слова OCR одной строкой (как для tokens) и где начинается каждое."""
    starts, parts, at = [], [], 0
    for w in words:
        starts.append(at)
        parts.append(w)
        at += len(w) + 1
    return " ".join(parts).replace("’", "'").replace("‘", "'"), starts


def _word_at(starts: list[int], pos: int) -> int:
    i = 0
    for k, s in enumerate(starts):
        if s <= pos:
            i = k
        else:
            break
    return i


def pick(
    words: Sequence[str],
    *,
    level: str = DEFAULT_LEVEL,
    names: Collection[str] = (),
    state: Callable[[str], StudyWord | None] = lambda _k: None,
    saved: RecallIndex | None = None,
) -> list[Pick]:
    """Над какими словами реплики (слова OCR по порядку) будут подсказки, по порядку в реплике.

    names: имена и названия игры (говорящие, глоссарий, что пишется с большой буквы): не трудные слова.
    state: что уже известно о слове (StudyBook.get). saved: сохранённые слова (свои и из Lexo)."""
    threshold = LEVELS.get(level, LEVELS[DEFAULT_LEVEL])
    tokens = list(words)
    out: list[Pick] = []
    used: set[int] = set()
    if saved is not None and len(saved):
        text, starts = _joined(tokens)
        spans = [(m.start(), m.end()) for m in _TOKEN.finditer(text)]
        for i, end, e in saved.spans(text):
            if len(out) >= MAX_MARKS:
                break
            first, last = _word_at(starts, spans[i][0]), _word_at(starts, spans[end - 1][0])
            if first in used:
                continue
            used.update(range(first, last + 1))
            label = e.word if len(e.parts) > 1 else ""
            out.append(Pick(first, clean(tokens[first]) or e.word, e.key, "saved", e.translation, label))
    caps = _caps_line(tokens)
    lowered = {n.casefold() for n in names}
    keys = {p.key for p in out}
    tests: list[Pick] = []
    news: list[tuple[int, Pick]] = []
    for i, token in enumerate(tokens):
        if i in used or not is_word(token) or is_stopword(token):
            continue
        core = destutter(clean(token).replace("’", "'"))
        if not core:
            continue
        base = core.split("'")[0]
        if core.casefold() in lowered or base.casefold() in lowered or _name_like(tokens, i, caps):
            continue
        w = norm(core)
        if not w or _junk(w) or not _hard(w, threshold):
            continue
        r = rank(w)
        if r is None and core[0].isupper() and not caps:
            continue  # «Monokuma's» в начале реплики: имя, которого нет в словаре
        key = w if "-" in w else lemma(w)
        if key in keys:
            continue
        st = state(key)
        if st is not None and st.state in ("known", "skip"):
            continue
        keys.add(key)
        if st is not None and st.state == "test":
            tests.append(Pick(i, core, key, "test"))
        else:
            news.append((_UNRANKED if r is None else r, Pick(i, core, key, "new")))
    out += tests[: max(0, MAX_MARKS - len(out))]
    news.sort(key=lambda x: -x[0])  # сначала самые редкие: без них реплику не понять
    out += [p for _r, p in news[:MAX_NEW]]
    return sorted(out, key=lambda p: p.index)


def match_meanings(picks: Iterable[Pick], glosses: Mapping[str, str]) -> dict[int, str]:
    """Значения от модели (слово -> значение) к словам реплики: по слову как есть или по словарной форме."""
    if not glosses:
        return {}
    by_lemma: dict[str, str] = {}
    for k, v in glosses.items():
        by_lemma.setdefault(lemma(k), v)
    out: dict[int, str] = {}
    for p in picks:
        if not p.ask:
            continue
        v = glosses.get(p.token.lower()) or glosses.get(norm(p.token)) or by_lemma.get(p.key) or by_lemma.get(lemma(p.token))
        if v:
            out[p.index] = v
    return out


class StudyBook:
    """Слова учёбы в памяти (из таблицы study_words): что уже знаешь, что под «?», значения."""

    def __init__(self, words: Iterable[StudyWord] = ()) -> None:
        self.words: dict[str, StudyWord] = {w.key: w for w in words}
        self._last = ""  # реплика, которую посчитали последней: F2 по той же второй раз не встреча

    def get(self, key: str) -> StudyWord | None:
        return self.words.get(key)

    def meaning(self, key: str) -> str:
        w = self.words.get(key)
        return w.meaning if w is not None and w.meaning != SKIP else ""

    def counts(self) -> dict[str, int]:
        out = {"new": 0, "test": 0, "known": 0}
        for w in self.words.values():
            if w.state in out and (w.seen or w.state != "new"):
                out[w.state] += 1
        return out

    def commit(self, shown: Shown, *, shown_s: float, now: float) -> tuple[list[StudyWord], int, bool]:
        """Реплика ушла с экрана: обновить слова.

        (изменённые слова для базы, сколько слов стало «знаю», засчитана ли реплика: была на экране
        не меньше MIN_SHOW_S и это не та же реплика, что в прошлый раз)."""
        norm_text = normalize_text(shown.text)
        counted = shown_s >= MIN_SHOW_S and norm_text != self._last
        changed: dict[str, StudyWord] = {}
        learned = 0
        for p in shown.picks:
            if p.kind == "saved":
                continue
            meaning = shown.meanings.get(p.index, "")
            existing = self.words.get(p.key)
            w = existing if existing is not None else StudyWord(p.key)
            before = (w.state, w.seen, w.passes, w.meaning)
            if meaning == SKIP:
                w.state = "skip"
            elif p.kind == "test" and p.index in shown.revealed:
                w.state, w.seen, w.passes = "new", SEEN_TO_TEST - BACK_SEEN, 0  # не вспомнил: ещё пару раз со значением
            elif meaning:
                w.meaning = meaning
                if counted and p.kind == "new":
                    w.seen += 1
                    if w.seen >= SEEN_TO_TEST:
                        w.state, w.passes = "test", 0
                elif counted and p.kind == "test" and not shown.peeked:
                    w.passes += 1
                    if w.passes >= PASSES_TO_KNOWN:
                        w.state = "known"
                        learned += 1
            if existing is None or (w.state, w.seen, w.passes, w.meaning) != before:
                w.updated_at = now
                self.words[p.key] = w
                changed[p.key] = w
        if counted:
            self._last = norm_text
        return list(changed.values()), learned, counted

    def forget(self, key: str) -> StudyWord | None:
        """Слово снова новое (вернуть подсказку)."""
        w = self.words.get(key)
        if w is None:
            return None
        w.state, w.seen, w.passes = "new", 0, 0
        return w
