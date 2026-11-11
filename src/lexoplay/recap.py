"""«Минута после игры»: несколько реплик только что сыгранной сессии с пропуском на месте слова.

Слово вспоминается в сцене, которую игрок только что прочитал: он помнит, кто это сказал и что
происходило, и новое слово цепляется за эту память. Отвечать надо одним нажатием: выбрать слово из трёх.
Варианты даны в словарной форме, так что окончание в реплике ответ не выдаёт.

В вопросы идут сначала слова, у которых игрок открывал «?» (забыл), потом новые за эту сессию (значение
было видно), потом те, где он подглядел перевод. То, что он вспомнил сам, не спрашиваем: это уже
повторено в игре. Меньше MIN_QUESTIONS вопросов не показываем вовсе: ради одного слова окно не нужно.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

from lexoplay.recall import variants
from lexoplay.store import Met

MAX_QUESTIONS = 5
MIN_QUESTIONS = 3
OPTIONS = 3
BLANK = "_____"
_PRIORITY = {"forgot": 0, "shown": 1, "peeked": 2}
_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")


@dataclass(frozen=True, slots=True)
class Question:
    key: str
    kind: str  # new | test | saved: чьё это слово (учёба или сохранённое)
    answer: str  # правильный вариант, как на кнопке
    options: tuple[str, ...]
    blanked: str  # реплика с пропуском
    sentence: str  # реплика целиком
    start: int  # где в sentence слово: для выделения после ответа
    end: int
    meaning: str
    translation: str


def _label(row: Met) -> str:
    """Как слово выглядит на кнопке: словарная форма слова, выражение как сохранено."""
    return row.key


def _find(sentence: str, row: Met) -> tuple[int, int] | None:
    """Где в реплике это место: сначала как оно стояло, потом по словоформам словарной формы."""
    for text in (row.word, row.key):
        text = text.strip()
        if not text:
            continue
        m = re.search(rf"(?<![A-Za-z]){re.escape(text)}(?![A-Za-z])", sentence, re.IGNORECASE)
        if m:
            return m.start(), m.end()
    if " " not in row.key:
        for m in _WORD.finditer(sentence):
            if row.key in variants(m.group(0).lower()):
                return m.start(), m.end()
    return None


def _in_sentence(label: str, sentence: str) -> bool:
    words = {f for m in _WORD.finditer(sentence) for f in variants(m.group(0).lower())}
    return all(part in words for part in label.lower().split())


def build(
    rows: list[Met],
    *,
    extra: list[str] | None = None,
    rng: random.Random | None = None,
    max_questions: int = MAX_QUESTIONS,
) -> list[Question]:
    """Вопросы по встречам одной сессии. extra: запасные слова для неверных вариантов, если своих мало."""
    rng = rng or random.Random()
    best: dict[str, Met] = {}
    rank: dict[str, int] = {}
    for r in rows:  # по порядку: для каждого слова остаётся последняя реплика, итог берём худший
        if not r.key or not r.meaning or not r.sentence:
            continue
        best[r.key] = r
        rank[r.key] = min(rank.get(r.key, 9), _PRIORITY.get(r.result, 9))
    pool = [_label(r) for r in best.values()]  # все слова сессии годятся в неверные варианты
    pool += [w for w in (extra or []) if w and w not in pool]
    asked = sorted((k for k in best if rank[k] < 9), key=lambda k: (rank[k], -best[k].at))
    out: list[Question] = []
    for key in asked:
        if len(out) >= max_questions:
            break
        row = best[key]
        span = _find(row.sentence, row)
        if span is None:
            continue  # OCR прочитал слово иначе: пропуск поставить некуда
        answer = _label(row)
        phrase = " " in answer
        others = [w for w in pool if w != answer and not _in_sentence(w, row.sentence)]
        # похожие по виду первыми: к слову слова, к выражению выражения
        rng.shuffle(others)
        others.sort(key=lambda w: (" " in w) != phrase)
        if len(others) < OPTIONS - 1:
            continue
        options = [answer, *others[: OPTIONS - 1]]
        rng.shuffle(options)
        start, end = span
        out.append(
            Question(
                key=key,
                kind=row.kind,
                answer=answer,
                options=tuple(options),
                blanked=row.sentence[:start] + BLANK + row.sentence[end:],
                sentence=row.sentence,
                start=start,
                end=end,
                meaning=row.meaning,
                translation=row.translation,
            )
        )
    return out if len(out) >= MIN_QUESTIONS else []


@dataclass(frozen=True, slots=True)
class Rare:
    """Полезное слово, которое игра больше не покажет: кандидат в карточки."""

    key: str
    word: str
    meaning: str
    sentence: str
    translation: str


def rare_words(
    rows: list[Met],
    *,
    total_in_novel,  # type: ignore[no-untyped-def]  # слово -> сколько раз во всей новелле
    rank,  # type: ignore[no-untyped-def]  # слово -> место в частотном списке или None
    known,  # type: ignore[no-untyped-def]  # слово -> уже сохранено
    limit: int = 6,
    max_total: int = 2,
    max_rank: int = 20_000,
) -> list[Rare]:
    """Новые слова сессии, которые в этой новелле почти не повторятся (игра сама им не научит), но в
    английском вообще нередкие, то есть пригодятся и дальше. Самые частые в языке первыми."""
    found: dict[str, tuple[int, Rare]] = {}
    for r in rows:
        if r.kind != "new" or " " in r.key or not r.meaning or not r.sentence or r.key in found:
            continue
        n = total_in_novel(r.key)
        place = rank(r.key)
        if not 1 <= n <= max_total or place is None or place >= max_rank or known(r.key):
            continue
        found[r.key] = (place, Rare(r.key, r.word, r.meaning, r.sentence, r.translation))
    return [rare for _place, rare in sorted(found.values(), key=lambda x: x[0])][:limit]
