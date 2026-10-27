"""Учёба: реплику читаешь по-английски сам, перевод спрятан под Alt, а над окном диалога строка
подсказок к тому, что мешает понять эту реплику на твоём уровне.

Что подсказывать, решает модель, которая переводит, в том же запросе: до трёх мест реплики, которые
игрок уровня B1 скорее всего не поймёт, со значением именно здесь. Чаще всего это не редкое слово, а
выражение из знакомых слов: «the way things are» (при нынешнем положении дел), «don't have the mind to»
(не настроены). Подсказка к одному «mind» там не помогла бы.

Каждое место (слово или выражение) проходит путь:

- новое: в строке подсказок видно значение, в тексте игры оно подчёркнуто; так первые SEEN_TO_TEST встреч;
- потом вместо значения «?»: вспомни сам. Не вспомнил, наведи курсор на «?» или на подчёркнутое
  место: значение откроется, слово ещё BACK_SEEN встречи будет со значением и (по желанию) уйдёт в
  карточки, раз оно никак не запоминается;
- «?» не открывал PASSES_TO_KNOWN раз и перевод реплики не подглядывал: знаешь, подсказки больше нет.

Сохранённые слова (свои и из Lexo) в реплике стоят жёлтым «?»: в игре их вспоминаешь, учишь в карточках.
Встреча считается, только если реплика была на экране хотя бы MIN_SHOW_S: пролистанную не читал.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Literal

from lexoplay.dedupe import normalize_text
from lexoplay.geometry import Rect
from lexoplay.rarity import _ranks, bases, norm
from lexoplay.recall import _TOKEN, RecallIndex, tokens
from lexoplay.store import StudyWord
from lexoplay.words import clean

LEVEL_NAMES: dict[str, str] = {"a2": "A2", "b1": "B1", "b2": "B2"}
DEFAULT_LEVEL = "b1"
MAX_HINTS = 4  # подсказок на реплику всего: больше уже мешает читать
MAX_FROM_MODEL = 3  # новых мест от модели
MAX_LEARNING = 2  # своих, что сейчас учатся (значение или «?»)
MAX_SAVED = 2  # сохранённых слов под жёлтым «?»
SEEN_TO_TEST = 4
PASSES_TO_KNOWN = 2
BACK_SEEN = 2
MIN_SHOW_S = 1.5
MATCH_RATIO = 0.8  # место от модели и слова OCR: насколько похожи (OCR бывает с ошибками)

Kind = Literal["new", "test", "saved"]


@dataclass(slots=True)
class Hint:
    """Подсказка к месту реплики: слово или выражение."""

    words: tuple[int, ...]  # слова OCR (ocr.words), под которыми линия; () если на экране не нашлось
    text: str  # как в реплике: «don't have the mind to»
    key: str  # для учёта: словарная форма слова (persuaded -> persuade) или выражение строчными
    kind: Kind  # new: значение видно | test: «?», вспомни | saved: твоё слово, жёлтый «?»
    meaning: str = ""
    model: bool = False  # подсказала модель для этой реплики (а не нашлось среди своих)


@dataclass(frozen=True, slots=True)
class StudyPlan:
    """Что известно о реплике до запроса к модели: свои подсказки и что модели объяснять не надо."""

    hints: tuple[Hint, ...] = ()
    known: tuple[str, ...] = ()  # места реплики, которые ты знаешь или уже учишь: модель их не берёт
    room: int = MAX_FROM_MODEL  # сколько новых мест можно от модели


@dataclass(slots=True)
class Shown:
    """Реплика в учёбе, пока она на экране: подсказки и что игрок открывал."""

    exe: str
    text: str
    words: tuple[str, ...]  # слова OCR по порядку (для поиска мест от модели)
    rects: tuple[Rect, ...]  # их рамки в долях окна игры
    since: float  # time.monotonic(), когда показали
    hints: list[Hint] = field(default_factory=list)  # что сейчас на экране: свои и от модели
    room: int = MAX_FROM_MODEL
    known: tuple[str, ...] = ()  # что модели не подсказывать (StudyPlan.known)
    base: list[Hint] = field(default_factory=list)  # свои подсказки (StudyPlan.hints): к ним добавляются от модели
    asked: bool = False  # подсказки модели пришли целиком (может быть, ни одной)
    pending: bool = False  # запрос подсказок в пути
    revealed: set[str] = field(default_factory=set)  # ключи открытых наведением «?»
    peeked: bool = False  # подглядывал перевод реплики
    title: str = ""
    session_id: str = ""
    translation: str = ""


def lemma(token: str) -> str:
    """Словарная форма для учёта: самая частая из форм слова (persuaded -> persuade, glasses -> glass)."""
    w = norm(token)
    ranks = _ranks()
    best, best_r = w, ranks.get(w)
    for b in sorted(bases(w)):
        r = ranks.get(b)
        if r is not None and (best_r is None or r < best_r):
            best, best_r = b, r
    return best


def item_key(text: str) -> str:
    """Ключ места: одно слово по словарной форме, выражение строчными как есть."""
    toks = tokens(text)
    if not toks:
        return ""
    return lemma(toks[0]) if len(toks) == 1 else " ".join(toks)


def _squash(text: str) -> str:
    return "".join(ch for ch in text.lower().replace("’", "'") if ch.isalnum())


def locate(text: str, words: Sequence[str], taken: Iterable[int] = ()) -> tuple[int, ...]:
    """Где в словах OCR место от модели. OCR мог разбить или склеить слово («m ind»), поэтому
    сравниваем буквы подряд, а окно берём на слово-два шире и уже."""
    target = _squash(text)
    n = len(tokens(text)) or 1
    if not target:
        return ()
    busy = set(taken)
    best_r, best = 0.0, ()
    for size in (n, n + 1, n - 1, n + 2):
        if size < 1:
            continue
        for i in range(len(words) - size + 1):
            idx = tuple(range(i, i + size))
            if busy.intersection(idx):
                continue
            cand = _squash(" ".join(words[i : i + size]))
            if not cand:
                continue
            r = SequenceMatcher(None, target, cand).ratio()
            if r > best_r + 1e-9:
                best_r, best = r, idx
    return best if best_r >= MATCH_RATIO else ()


def _joined(words: Sequence[str]) -> tuple[str, list[int]]:
    """Слова OCR одной строкой (как для recall.tokens) и где начинается каждое."""
    starts, at = [], 0
    for w in words:
        starts.append(at)
        at += len(w) + 1
    return " ".join(words).replace("’", "'").replace("‘", "'"), starts


def _word_at(starts: list[int], pos: int) -> int:
    i = 0
    for k, s in enumerate(starts):
        if s <= pos:
            i = k
        else:
            break
    return i


def _found(index: RecallIndex, words: Sequence[str]) -> list[tuple[tuple[int, ...], str, object]]:
    """Записи index в словах OCR: (слова, текст на экране, запись)."""
    text, starts = _joined(words)
    spans = [(m.start(), m.end()) for m in _TOKEN.finditer(text)]
    out = []
    for i, end, e in index.spans(text):
        first, last = _word_at(starts, spans[i][0]), _word_at(starts, spans[end - 1][0])
        idx = tuple(range(first, last + 1))
        shown = " ".join(clean(words[k]) for k in idx).strip() or e.word
        out.append((idx, shown, e))
    return out


def plan(
    words: Sequence[str],
    *,
    learning: RecallIndex | None = None,
    state: Callable[[str], StudyWord | None] = lambda _k: None,
    saved: RecallIndex | None = None,
) -> StudyPlan:
    """Свои подсказки к реплике (слова OCR по порядку) до запроса к модели.

    learning: места из учёбы (StudyBook.index), state: что о них известно, saved: сохранённые слова."""
    hints: list[Hint] = []
    known: list[str] = []
    used: set[int] = set()
    if learning is not None and len(learning):
        for idx, shown, e in _found(learning, words):
            key = e.word  # type: ignore[attr-defined]
            st = state(key)
            if st is None or (st.state in ("new", "test") and not st.meaning):
                continue  # без значения подсказать нечего: пусть модель объяснит заново
            known.append(shown)
            if st.state not in ("new", "test") or used.intersection(idx):
                continue
            if sum(1 for h in hints if h.kind != "saved") >= MAX_LEARNING:
                continue
            used.update(idx)
            hints.append(Hint(idx, shown, key, "test" if st.state == "test" else "new", st.meaning))
    if saved is not None and len(saved):
        n = 0
        for idx, shown, e in _found(saved, words):
            if used.intersection(idx):
                continue
            label = e.word if len(e.parts) > 1 else shown  # type: ignore[attr-defined]
            known.append(label)
            if n >= MAX_SAVED:
                continue
            n += 1
            used.update(idx)
            hints.append(Hint(idx, label, e.key, "saved", e.translation))  # type: ignore[attr-defined]
    learning_n = sum(1 for h in hints if h.kind != "saved")
    room = max(1, min(MAX_FROM_MODEL, MAX_HINTS - learning_n))
    return StudyPlan(tuple(sorted(hints, key=lambda h: h.words[:1])), tuple(dict.fromkeys(known)), room)


def model_hints(
    words: Sequence[str],
    items: Iterable[tuple[str, str]],
    *,
    state: Callable[[str], StudyWord | None] = lambda _k: None,
    have: Iterable[Hint] = (),
    room: int = MAX_FROM_MODEL,
) -> list[Hint]:
    """Места, которые подсказала модель («часть реплики», значение), к словам на экране."""
    have = list(have)
    keys = {h.key for h in have}
    taken = {i for h in have for i in h.words}
    out: list[Hint] = []
    for text, meaning in items:
        if len(out) >= room:
            break
        key = item_key(text)
        if not key or not meaning or key in keys:
            continue
        st = state(key)
        if st is not None and st.state in ("known", "skip"):
            continue
        idx = locate(text, words, taken)
        taken.update(idx)
        keys.add(key)
        out.append(Hint(idx, text.strip(), key, "test" if st is not None and st.state == "test" else "new", meaning, True))
    return out


def merge(have: list[Hint], new: list[Hint]) -> list[Hint]:
    """Подсказки реплики вместе: свои учимые, потом от модели, потом сохранённые; не больше MAX_HINTS."""
    learning = [h for h in have if h.kind != "saved"]
    saved = [h for h in have if h.kind == "saved"]
    out = (learning + new + saved)[:MAX_HINTS]
    return sorted(out, key=lambda h: h.words[:1] or (10_000,))


class StudyBook:
    """Места учёбы в памяти (из таблицы study_words): что знаешь, что под «?», значения."""

    def __init__(self, words: Iterable[StudyWord] = ()) -> None:
        self.words: dict[str, StudyWord] = {w.key: w for w in words}
        self.version = 0
        self._index: tuple[int, RecallIndex] | None = None
        self._last = ""  # реплика, которую посчитали последней: F2 по той же второй раз не встреча

    def get(self, key: str) -> StudyWord | None:
        return self.words.get(key)

    def meaning(self, key: str) -> str:
        w = self.words.get(key)
        return w.meaning if w is not None else ""

    def index(self) -> RecallIndex:
        """Все места учёбы для поиска в реплике (по словоформам, как сохранённые слова)."""
        if self._index is None or self._index[0] != self.version:
            entries = {k: (w.meaning or "-") for k, w in self.words.items() if w.state in ("new", "test", "known")}
            self._index = (self.version, RecallIndex([], lexo=entries))
        return self._index[1]

    def known_count(self) -> int:
        return sum(1 for w in self.words.values() if w.state == "known")

    def commit(self, shown: Shown, *, shown_s: float, now: float) -> tuple[list[StudyWord], int, bool, list[Hint]]:
        """Реплика ушла с экрана: обновить места.

        (изменённые для базы, сколько стало «знаю», засчитана ли реплика, забытые: открывал их «?»)."""
        norm_text = normalize_text(shown.text)
        counted = shown_s >= MIN_SHOW_S and norm_text != self._last
        changed: dict[str, StudyWord] = {}
        learned = 0
        forgotten: list[Hint] = []
        for h in shown.hints:
            if h.kind == "saved":
                continue
            existing = self.words.get(h.key)
            w = existing if existing is not None else StudyWord(h.key)
            before = (w.state, w.seen, w.passes, w.meaning)
            if h.kind == "test" and h.key in shown.revealed:
                w.state, w.seen, w.passes = "new", SEEN_TO_TEST - BACK_SEEN, 0  # не вспомнил: ещё пару раз со значением
                forgotten.append(h)
            elif h.meaning:
                w.meaning = h.meaning if h.model or not w.meaning else w.meaning
                if counted and h.kind == "new":
                    w.seen += 1
                    if w.seen >= SEEN_TO_TEST:
                        w.state, w.passes = "test", 0
                elif counted and h.kind == "test" and not shown.peeked:
                    w.passes += 1
                    if w.passes >= PASSES_TO_KNOWN:
                        w.state = "known"
                        learned += 1
            if existing is None or (w.state, w.seen, w.passes, w.meaning) != before:
                w.updated_at = now
                self.words[h.key] = w
                changed[h.key] = w
        if changed:
            self.version += 1
        if counted:
            self._last = norm_text
        return list(changed.values()), learned, counted, forgotten
