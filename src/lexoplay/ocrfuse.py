"""Склейка чтений OCR разными способами: слова, которые лучший способ пропустил, берём из других.

Типичный случай: в новелле ключевые слова выделены другим цветом (жёлтые с оранжевой
обводкой среди голубого текста). Один способ подготовки картинки читает голубой текст, но
теряет жёлтые слова, другой наоборот. Мусора при этом нет, просто слов не хватает, и
перевод получается обрезанным. Поэтому берём лучшее чтение целиком и добавляем в него
слова из остальных, которые лежат там, где в лучшем ничего не прочитано.
"""

from __future__ import annotations

from lexoplay.geometry import Rect
from lexoplay.ocr_model import Line, OcrResult, Word
from lexoplay.ocrscore import token_score

MIN_GOOD = 0.7  # добавляем только похожее на слово: из словаря или имя с заглавной


def _overlap(a: Rect, b: Rect) -> float:
    """Доля пересечения от меньшей рамки."""
    x1, y1 = max(a.x, b.x), max(a.y, b.y)
    x2, y2 = min(a.right, b.right), min(a.bottom, b.bottom)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    small = min(a.w * a.h, b.w * b.h)
    return inter / small if small > 0 else 0.0


def _good(word: Word, lang: str) -> bool:
    weight, score = token_score(word.text, lang)
    return weight > 0 and score >= MIN_GOOD


def _line_of(lines: list[list[Word]], word: Word) -> int | None:
    """Строка, в которую слово попадает по высоте."""
    cy = word.rect.y + word.rect.h / 2
    for i, ws in enumerate(lines):
        if not ws:
            continue
        top = min(w.rect.y for w in ws)
        bottom = max(w.rect.bottom for w in ws)
        pad = (bottom - top) * 0.3
        if top - pad <= cy <= bottom + pad:
            return i
    return None


def fuse(base: OcrResult, others: list[OcrResult], lang: str = "en") -> tuple[OcrResult, int]:
    """(склеенное чтение, сколько слов добавлено)."""
    lines = [list(line.words) for line in base.lines]
    placed = [w for ws in lines for w in ws]
    added = 0
    pending: list[Word] = []
    for other in others:
        for word in other.words:
            if not _good(word, lang):
                continue
            if any(_overlap(word.rect, p.rect) > 0.3 for p in placed):
                continue  # это место уже прочитано
            i = _line_of(lines, word)
            if i is None:
                pending.append(word)
            else:
                lines[i].append(word)
                lines[i].sort(key=lambda w: w.rect.x)
                added += 1
            placed.append(word)
    # целые строки, которых в лучшем чтении нет: только если в них хотя бы два слова
    new_lines: list[list[Word]] = []
    for word in sorted(pending, key=lambda w: w.rect.y):
        i = _line_of(new_lines, word)
        if i is None:
            new_lines.append([word])
        else:
            new_lines[i].append(word)
    for ws in new_lines:
        if len(ws) >= 2:
            ws.sort(key=lambda w: w.rect.x)
            lines.append(ws)
            added += len(ws)
    if not added:
        return base, 0
    lines = [ws for ws in lines if ws]
    lines.sort(key=lambda ws: min(w.rect.y for w in ws))
    return OcrResult(tuple(Line(tuple(ws)) for ws in lines)), added
