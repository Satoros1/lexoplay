"""Простой английский синоним к сложному слову: «rebuttal ≈ argument against».

Синоним в карточке помогает, только если он уже знаком: новое слово цепляется за известное.
Если синоним сам сложный, это ещё одно слово, которое придётся учить, и два похожих новых
слова путаются. Поэтому строку из модели проверяем по частотному списку и выбрасываем,
если в ней есть редкое слово или если она не проще самого слова.
"""

from __future__ import annotations

import re

from lexoplay.rarity import rank

MAX_RANK = 4000  # слова синонима из самых частых: уровень примерно до B1
COMMON_WORD = 1500  # само слово и так частое: синоним к нему не нужен
MAX_WORDS = 6
_ALLOWED = re.compile(r"^[A-Za-z][A-Za-z' ,/()-]*$")
_WORD = re.compile(r"[A-Za-z]+(?:'[a-z]+)?")
_LEAD = re.compile(r"^(?:≈|~|=|approx\.?)\s*", re.I)
# служебные слова в пояснении («not wanting to», «an answer that...»): их редкость не считаем
_FUNCTION = frozenset("a an the to of for at in on by with from as or and not no sb sth someone something one's".split())


def simple_hint(text: str | None, lemma: str) -> str:
    """Синоним или короткое пояснение простыми словами, либо пустая строка, если он не помогает."""
    t = _LEAD.sub("", (text or "").strip().strip("\"'«»").strip())
    t = re.sub(r"\s+", " ", t).strip(" .;")
    if not t or not _ALLOWED.match(t):
        return ""
    words = [w.lower() for w in _WORD.findall(t)]
    if not words or len(words) > MAX_WORDS:
        return ""
    lem = lemma.strip().lower()
    lem_words = lem.split()
    # то же слово или однокоренное («rebuttal ≈ to rebut»): ничего не объясняет
    for w in words:
        if w in lem_words or any(len(x) >= 5 and len(w) >= 5 and (x.startswith(w[:5]) or w.startswith(x[:5])) for x in lem_words):
            return ""
    target = rank(lem) if len(lem_words) == 1 else None
    if target is not None and target < COMMON_WORD:
        return ""
    content = [w for w in words if w not in _FUNCTION]
    if not content:
        return ""
    for w in content:
        r = rank(w)
        if r is None or r > MAX_RANK:
            return ""
    if len(content) == 1 and target is not None and (rank(content[0]) or 0) >= target:
        return ""  # синоним не проще самого слова
    return t
