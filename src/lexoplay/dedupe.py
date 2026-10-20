"""Память реплик сессии: дедуп (повторный F2 бесплатно) и контекст для перевода."""

from __future__ import annotations

import unicodedata
from collections import deque
from dataclasses import dataclass
from difflib import SequenceMatcher


def normalize_text(text: str) -> str:
    """Нижний регистр, без пунктуации, одинарные пробелы. Ключ для дедупа и кеша."""
    out = []
    for ch in text.lower():
        cat = unicodedata.category(ch)
        if cat.startswith("P") or cat.startswith("S"):
            out.append(" ")
        else:
            out.append(ch)
    return " ".join("".join(out).split())


def similarity(a: str, b: str) -> float:
    """0..1. OCR между кадрами чуть плавает (l/I, пропущенная запятая), поэтому не точное равенство."""
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


@dataclass(frozen=True, slots=True)
class LineRecord:
    line_id: str
    speaker: str | None
    source: str  # исправленный моделью английский
    translation: str
    norm: str  # нормализованный сырой OCR, по нему ищем повтор


class LineMemory:
    """Последние N реплик сессии. Повтор той же реплики берём отсюда без запроса."""

    def __init__(self, size: int = 20, threshold: float = 0.92) -> None:
        self._items: deque[LineRecord] = deque(maxlen=size)
        self._threshold = threshold

    def find(self, raw_text: str) -> LineRecord | None:
        norm = normalize_text(raw_text)
        if not norm:
            return None
        best: LineRecord | None = None
        best_score = 0.0
        for rec in reversed(self._items):
            score = similarity(norm, rec.norm)
            if score > best_score:
                best, best_score = rec, score
        return best if best_score >= self._threshold else None

    def add(self, rec: LineRecord) -> None:
        self._items.append(rec)

    def replace(self, rec: LineRecord) -> None:
        """Реплику перевели заново: старый перевод из памяти убрать, новый положить последним."""
        kept = [r for r in self._items if r.norm != rec.norm]
        self._items.clear()
        self._items.extend(kept)
        self._items.append(rec)

    def context(self, limit: int = 8) -> list[LineRecord]:
        """Последние реплики от старых к новым — контекст для модели."""
        items = list(self._items)
        return items[-limit:]

    def clear(self) -> None:
        self._items.clear()

    def __len__(self) -> int:
        return len(self._items)
