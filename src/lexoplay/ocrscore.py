"""Насколько хорошо OCR прочитал реплику: доля похожих на настоящие слов.

Нужна, чтобы LexoPlay сам выбирал, как готовить картинку для OCR. Обычный текст
лучше читается после мягкого увеличения, пиксельный шрифт (Hotline Miami, ретро-игры)
после увеличения «по пикселям» и чёрно-белой картинки, цветные буквы на тёмном фоне
после выделения яркости. Пробуем, считаем оценку, лучший способ запоминаем для игры.

Оценка не про смысл, а про «мусор»: слова из словаря частых английских слов считаются
хорошими, слова с цифрами и значками внутри («he1lo», «w@s», «|») плохими.
Для других языков словаря нет, там смотрим только на мусор и регистр букв.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from lexoplay.ocr_model import OcrResult

_EDGE = ".,!?…:;\"'“”‘’«»()[]{}<>-–—*~_"
_SINGLE_OK = {"a", "i", "o"}
_VOWELS = set("aeiouy")


@dataclass(frozen=True, slots=True)
class Quality:
    good: float  # «хорошие» буквы
    total: float  # все буквы и значки, кроме обычной пунктуации
    junk: float = 0.0  # буквы в явном мусоре: «he1lo», «|», «hElLo»

    @property
    def ratio(self) -> float:
        return self.good / self.total if self.total else 0.0

    @property
    def rank(self) -> float:
        """Для сравнения способов: больше хорошего текста и меньше мусора."""
        return self.good - 0.6 * (self.total - self.good)

    @property
    def ok(self) -> bool:
        """Прочитано уверенно: другие способы можно не пробовать.

        Имена и сленг не мусор: строка «My name's Makoto Naegi.» читается уверенно,
        хотя половины слов нет в словаре. Плохо, когда есть мусор."""
        return self.good >= 6 and self.ratio >= 0.6 and self.junk <= self.total * 0.08

    def __str__(self) -> str:
        return f"{self.ratio:.2f}/{self.good:.0f}"


def _words() -> frozenset[str]:
    from lexoplay.wordlist_en import WORDS

    return WORDS


def _sane_case(core: str) -> bool:
    """«Hello», «hello», «HELLO» нормально; «hElLo» и «HeLLo» это OCR."""
    letters = [c for c in core if c.isalpha()]
    if len(letters) <= 1:
        return True
    rest = letters[1:]
    return all(c.islower() for c in rest) or all(c.isupper() for c in rest)


def token_score(token: str, lang: str = "en") -> tuple[float, float]:
    """(вес, доля хорошего 0..1) для одного слова OCR."""
    core = token.strip(_EDGE).replace("’", "'")
    if not core:
        return 0.0, 0.0  # «...», «-», «!?»: обычная пунктуация
    letters = sum(1 for c in core if c.isalpha())
    digits = sum(1 for c in core if c.isdigit())
    other = len(core) - letters - digits - core.count("'") - core.count("-")
    weight = float(len(core))
    if letters == 0:
        if digits and not other:
            return weight, 0.6  # числа бывают в репликах
        return weight, 0.0  # «|», «#», «©»
    if digits or other:
        return weight, 0.0  # «he1lo», «w@s»
    low = core.lower()
    if len(low) == 1:
        return 1.0, 1.0 if low in _SINGLE_OK else 0.15
    if not _sane_case(core):
        return weight, 0.1
    if lang != "en":
        return weight, 0.85
    words = _words()
    if low in words:
        return weight, 1.0
    parts = [p for p in re.split(r"[-']", low) if p]
    if parts and all(p in words or len(p) <= 2 for p in parts) and any(len(p) > 2 for p in parts):
        return weight, 1.0  # «well-known», «Mira's»
    for suffix in ("s", "es", "ed", "ing", "ly", "er"):
        if low.endswith(suffix) and low[: -len(suffix)] in words:
            return weight, 1.0
    # не из словаря: имя, сленг или ошибка OCR. Без гласных почти наверняка ошибка.
    if not any(c in _VOWELS for c in low):
        return weight, 0.1
    return weight, 0.7 if core[:1].isupper() else 0.4


def text_quality(text: str, lang: str = "en") -> Quality:
    good = total = junk = 0.0
    for tok in text.split():
        w, g = token_score(tok, lang)
        good += w * g
        total += w
        if g < 0.2:
            junk += w
    return Quality(good, total, junk)


def quality(result: OcrResult, lang: str = "en") -> Quality:
    return text_quality(result.raw_text, lang)
