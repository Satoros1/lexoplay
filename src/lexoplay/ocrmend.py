"""Склейка слов, которые OCR разрезал пробелом: «masterm ind» -> «mastermind», «the ir» -> «their».

В некоторых играх (Danganronpa) шрифт рисует буквы с неровными промежутками, и Windows OCR видит
пробел посреди слова. Перевод модель всё равно понимает, но подсказки учёбы ломаются: модель
берёт обрубок «ind» как отдельное слово, а подчёркивание начинается с середины слова.

Склеиваем два соседних слова строки, если:
- один кусок вообще не слово («masterm», «ily»), а вместе слово из словаря частых слов, его форма или
  сложное из двух словарных (master + mind), разделённое не там, где пробел;
- или оба куска слова, но второй типичный обрубок конца слова («ing», «ir», «re», «ed»), а вместе
  слово прямо из словаря («com ing», «the ir»).
Два обычных слова («to ward», «a shore», «a go») не трогаем, сокращения («You're late») тоже.
Только для английского: словарь английский.
"""

from __future__ import annotations

from functools import lru_cache

from lexoplay.geometry import Rect
from lexoplay.ocr_model import Line, OcrResult, Word

# обрубки конца слова, которые в словаре есть, но отдельным словом в реплике не стоят
ENDINGS = frozenset(
    {
        "ing",
        "ings",
        "ed",
        "er",
        "ers",
        "es",
        "ly",
        "ir",
        "re",
        "ion",
        "ions",
        "tion",
        "ment",
        "ments",
        "ness",
        "ful",
        "ous",
        "ity",
        "ive",
        "ind",
    }
)
MIN_PART = 3  # сложное слово из двух словарных: каждое не короче


@lru_cache(maxsize=1)
def _ranks() -> dict[str, int]:
    from lexoplay.wordlist_en import RANKED

    return {w: i for i, w in enumerate(RANKED)}


def _word(word: str) -> bool:
    """Слово из словаря или его форма (demanded -> demand)."""
    from lexoplay.rarity import bases

    ranks = _ranks()
    return word in ranks or any(b in ranks for b in bases(word))


def _known(word: str, cut: int) -> bool:
    """Склеенное слово настоящее. cut: где был пробел OCR. Сложное из двух словарных (master + mind)
    засчитываем, только если делится не там, где пробел: иначе это просто два слова («and demanded»)."""
    if _word(word):
        return True
    ranks = _ranks()
    return any(
        i != cut and word[:i] in ranks and _word(word[i:])
        for i in range(MIN_PART, len(word) - MIN_PART + 1)
    )


def _tail(text: str) -> tuple[str, str]:
    """«Hina...com» -> («Hina...», «com»): буквы в конце слова и всё до них."""
    i = len(text)
    while i > 0 and text[i - 1].isalpha():
        i -= 1
    return text[:i], text[i:]


def _head(text: str) -> tuple[str, str]:
    """«ind,» -> («ind», «,»); «family's» -> («family», «'s»)."""
    i = 0
    while i < len(text) and text[i].isalpha():
        i += 1
    return text[:i], text[i:]


def _join(a: Word, b: Word) -> Word | None:
    pre, tail = _tail(a.text)
    head, post = _head(b.text)
    if not tail or not head or not head[0].islower():
        return None  # после знака или перед словом с заглавной это два слова
    if pre and pre[-1] in "'’":
        return None  # «You're late»: «re» тут сокращение, а не обрубок
    if post[:1] in ("'", "’") and post[1:2].isalpha() and post[1:2] != "s":
        return None  # «ain't», «aren't»: второе слово сокращение, целое
    t, h = tail.lower(), head.lower()
    if _word(t) and _word(h):
        # оба куска сами слова: склеиваем, только если второй типичный обрубок конца слова («com ing»,
        # «the ir», «the re»), а вместе слово прямо из словаря. «to ward», «a shore», «the seed» так и остаются
        if h not in ENDINGS or (t + h) not in _ranks():
            return None
    elif not _known(t + h, len(t)):
        return None
    r1, r2 = a.rect, b.rect
    x, y = min(r1.x, r2.x), min(r1.y, r2.y)
    rect = Rect(x, y, max(r1.right, r2.right) - x, max(r1.bottom, r2.bottom) - y)
    return Word(pre + tail + head + post, rect)


def mend(result: OcrResult, lang: str = "en") -> OcrResult:
    """Склеить разрезанные слова в каждой строке. Ничего не склеилось: тот же объект."""
    if lang != "en":
        return result
    lines: list[Line] = []
    changed = False
    for line in result.lines:
        out: list[Word] = []
        for w in line.words:
            if out:
                joined = _join(out[-1], w)
                if joined is not None:
                    out[-1] = joined
                    changed = True
                    continue
            out.append(w)
        lines.append(Line(tuple(out)) if len(out) != len(line.words) else line)
    return OcrResult(tuple(lines)) if changed else result
