"""Результат OCR в своих моделях: не зависим от winrt за пределами win/ocr.py.

Рамки слов храним нормализованными (0..1 от клиентской области игры), чтобы в F3 их
можно было нарисовать поверх игры при любом масштабе и под Magpie.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lexoplay.geometry import Rect


@dataclass(frozen=True, slots=True)
class Word:
    text: str
    rect: Rect  # нормализованная


@dataclass(frozen=True, slots=True)
class Line:
    words: tuple[Word, ...]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def rect(self) -> Rect:
        if not self.words:
            return Rect(0, 0, 0, 0)
        x1 = min(w.rect.x for w in self.words)
        y1 = min(w.rect.y for w in self.words)
        x2 = max(w.rect.right for w in self.words)
        y2 = max(w.rect.bottom for w in self.words)
        return Rect(x1, y1, x2 - x1, y2 - y1)


@dataclass(frozen=True, slots=True)
class OcrResult:
    lines: tuple[Line, ...] = field(default_factory=tuple)

    @property
    def words(self) -> list[Word]:
        return [w for line in self.lines for w in line.words]

    @property
    def raw_text(self) -> str:
        return "\n".join(line.text for line in self.lines)
