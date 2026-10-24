"""Насколько слово редкое: по частотному списку (wordlist_en), для подсветки трудных слов в режиме слов.

Ранг слова: его место в списке, а для словоформы место самой частой из её форм (talked -> talk).
Уровни грубо соответствуют CEFR по частоте: B1 примерно с 2000-го слова, B2 с 4000-го, C1 с 9000-го.
Слова, которых в списке нет совсем, считаем редкими, кроме мусора вроде «Ahhhh» и коротких междометий.
"""

from __future__ import annotations

import re
from functools import lru_cache

from lexoplay.words import IRREGULAR, clean

LEVELS: dict[str, int] = {"b1": 2000, "b2": 4000, "c1": 9000}
LEVEL_NAMES: dict[str, str] = {"b1": "B1+", "b2": "B2+", "c1": "C1+"}
COMMON = 1500  # самые частые слова: подчёркиваем еле заметно

# междометия и звуки: в частотном списке они бывают далеко, но учить их незачем
INTERJECTIONS = frozenset(
    """
ah ahh aah aw aww argh bah eh eek er erm gah gee geez jeez ha haha hah heh hehe hm hmm hmph huh mhm meh mm mmm
nah nope oh ohh ooh oops ouch ow pfft phew psst sheesh shh tsk ugh uh uhh um umm welp whoa wow yay yeah yep yikes
""".split()
)

# неправильные формы, у которых есть своё частое значение: «a bit» не «укусил», «left» не только «ушёл»
AMBIGUOUS = frozenset("bit left lay rose saw felt fell found lives better best worse worst".split())

_REPEAT = re.compile(r"(.)\1\1")  # ahhh, nooo
_STUTTER = re.compile(r"(\w{1,3})\1{2,}")  # hahaha
_VOWEL = re.compile(r"[aeiouy]")


@lru_cache(maxsize=1)
def _ranks() -> dict[str, int]:
    from lexoplay.wordlist_en import RANKED

    return {w: i for i, w in enumerate(RANKED)}


def norm(token: str) -> str:
    return clean(token.replace("’", "'").replace("‘", "'")).lower()


_CONTRACTIONS = ("n't", "'d", "'ll", "'ve", "'re", "'m")
_NOT_BASES = {"won't": "will", "can't": "can", "shan't": "shall", "ain't": "be"}


def bases(word: str, *, irregular: bool = True) -> set[str]:
    """Словарные формы по окончаниям словоизменения (без -er/-est/-ly: bitter не bit)."""
    w = word
    out: set[str] = set()
    if w.endswith("'s"):
        w = w[:-2]
        out.add(w)
    if irregular and w in IRREGULAR and w not in AMBIGUOUS:
        out.add(IRREGULAR[w])

    def add(base: str) -> None:
        if len(base) < 2:
            return
        out.add(base)
        out.add(base + "e")
        if len(base) > 2 and base[-1] == base[-2] and base[-1] not in "lsz":
            out.add(base[:-1])  # stopped -> stop

    for tail in _CONTRACTIONS:  # she'd, they've, couldn't: слово как у she, they, could
        if w.endswith(tail) and len(w) > len(tail):
            out.add(_NOT_BASES.get(w, w[: -len(tail)]))
            break
    n = len(w)
    if w.endswith("ies") and n > 4:
        out.add(w[:-3] + "y")
    elif w.endswith("es") and n > 3:
        out.update((w[:-2], w[:-1]))
    elif w.endswith("s") and not w.endswith(("ss", "us", "is")) and n > 3:
        out.add(w[:-1])
    if w.endswith("ied") and n > 4:
        out.add(w[:-3] + "y")
    elif w.endswith("ed") and n > 3:
        add(w[:-2])
    if w.endswith("ing") and n > 4:
        add(w[:-3])
    out.discard("")
    out.discard(word)
    return out


def rank(token: str) -> int | None:
    """Место слова в частотном списке (0 самое частое) или None, если его там нет."""
    w = norm(token)
    if not w:
        return None
    ranks = _ranks()
    best = ranks.get(w)
    for b in bases(w):
        r = ranks.get(b)
        if r is not None and (best is None or r < best):
            best = r
    return best


def _junk(w: str) -> bool:
    return (
        w in INTERJECTIONS
        or bool(_REPEAT.search(w))
        or bool(_STUTTER.fullmatch(w))
        or not _VOWEL.search(w)
        or any(ch.isdigit() for ch in w)
    )


def tier(token: str, level: str | None) -> str:
    """'rare' (уровня level и выше), 'common' (из самых частых) или '' (обычное либо подсветка выключена)."""
    if level not in LEVELS:
        return ""
    w = norm(token)
    if not w or _junk(w):
        return ""
    if "-" in w:  # well-known, half-baked: редкое, если редкая хоть одна часть
        parts = [tier(p, level) for p in w.split("-") if p]
        if "rare" in parts:
            return "rare"
        return "common" if parts and all(t == "common" for t in parts) else ""
    r = rank(w)
    if r is None:
        if len(w) < 4 or "'" in w:  # y'know, ma'am: разговорное, а не трудное
            return ""
        return "rare"
    if r >= LEVELS[level]:
        return "rare"
    return "common" if r < COMMON else ""
