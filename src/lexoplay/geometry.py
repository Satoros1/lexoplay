"""Координаты. Здесь три системы, и путать их нельзя:

* нормализованная (0..1) — доля от клиентской области игры; так хранится область диалога;
* кадр — пиксели захваченного изображения (например 800×600);
* экран — физические пиксели монитора, где игра реально нарисована (display_rect).

Модуль чистый: никаких Windows-вызовов, всё покрыто тестами.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def empty(self) -> bool:
        return self.w <= 0 or self.h <= 0

    def to_int(self) -> tuple[int, int, int, int]:
        return round(self.x), round(self.y), round(self.w), round(self.h)

    def to_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}

    @staticmethod
    def from_dict(d: dict[str, float]) -> Rect:
        return Rect(float(d["x"]), float(d["y"]), float(d["w"]), float(d["h"]))

    @staticmethod
    def from_points(x1: float, y1: float, x2: float, y2: float) -> Rect:
        """Прямоугольник по двум углам в любом порядке (как тянет мышь)."""
        return Rect(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))


def clamp01(v: float) -> float:
    return 0.0 if v < 0 else 1.0 if v > 1 else v


def normalize(rect: Rect, space: Rect) -> Rect:
    """Прямоугольник в пространстве `space` -> доли 0..1, обрезанные по краям."""
    if space.empty:
        raise ValueError("empty space")
    x1 = clamp01((rect.x - space.x) / space.w)
    y1 = clamp01((rect.y - space.y) / space.h)
    x2 = clamp01((rect.right - space.x) / space.w)
    y2 = clamp01((rect.bottom - space.y) / space.h)
    return Rect(x1, y1, x2 - x1, y2 - y1)


def denormalize(norm: Rect, space: Rect) -> Rect:
    """Доли 0..1 -> прямоугольник в пространстве `space`."""
    return Rect(
        space.x + norm.x * space.w,
        space.y + norm.y * space.h,
        norm.w * space.w,
        norm.h * space.h,
    )


def fit_aspect(src_w: float, src_h: float, dst: Rect) -> Rect:
    """Вписать картинку src_w×src_h в dst с сохранением пропорций, по центру.

    Так Magpie по умолчанию растягивает 800×600 на 2560×1440: 1920×1440 и полосы по бокам.
    """
    if src_w <= 0 or src_h <= 0 or dst.empty:
        return dst
    scale = min(dst.w / src_w, dst.h / src_h)
    w, h = src_w * scale, src_h * scale
    return Rect(dst.x + (dst.w - w) / 2, dst.y + (dst.h - h) / 2, w, h)


def place_strip(region: Rect, strip_h: float, bounds: Rect, gap: float = 8.0) -> Rect:
    """Где рисовать строку перевода: над областью диалога, а если сверху мало места, под ней.

    Все величины в одной системе (логические пиксели оверлея). Ширина = ширина области.
    Если не влезает ни сверху, ни снизу, прижимаем к верху области внутри неё.
    """
    above_y = region.y - gap - strip_h
    if above_y >= bounds.y:
        return Rect(region.x, above_y, region.w, strip_h)
    below_y = region.bottom + gap
    if below_y + strip_h <= bounds.bottom:
        return Rect(region.x, below_y, region.w, strip_h)
    return Rect(region.x, max(bounds.y, region.y), region.w, strip_h)


def font_px_for(display_h_logical: float, base_px: float = 19.0, scale: float = 1.0) -> float:
    """Размер шрифта строки: base_px при высоте игры 1080, пропорционально дальше.

    Считаем в логических пикселях оверлея, поэтому на мониторе 150% физический размер
    остаётся привязанным к высоте игры. Меньше 14 не опускаемся: дальше не читается.
    """
    return max(14.0, base_px * display_h_logical / 1080.0 * scale)
