"""Картинка перед OCR, сравнение кадров и проверка «чёрного» захвата. Только Pillow."""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageChops, ImageOps, ImageStat

from lexoplay.geometry import Rect, denormalize


@dataclass(frozen=True, slots=True)
class Prepared:
    image: Image.Image  # то, что уходит в OCR
    scale: float  # во сколько раз увеличили обрезку
    crop: Rect  # обрезка в пикселях кадра
    frame_w: int
    frame_h: int

    def to_norm(self, x: float, y: float, w: float, h: float) -> Rect:
        """Рамка из координат подготовленной картинки -> нормализованная (0..1 от кадра)."""
        fx = self.crop.x + x / self.scale
        fy = self.crop.y + y / self.scale
        return Rect(fx / self.frame_w, fy / self.frame_h, (w / self.scale) / self.frame_w, (h / self.scale) / self.frame_h)


def crop_norm(frame: Image.Image, region: Rect) -> tuple[Image.Image, Rect]:
    """Вырезать нормализованную область из кадра. Возвращает обрезку и её рамку в пикселях."""
    r = denormalize(region, Rect(0, 0, frame.width, frame.height))
    x, y, w, h = r.to_int()
    x = max(0, min(x, frame.width - 1))
    y = max(0, min(y, frame.height - 1))
    w = max(1, min(w, frame.width - x))
    h = max(1, min(h, frame.height - y))
    return frame.crop((x, y, x + w, y + h)), Rect(x, y, w, h)


def upscale_factor(frame_h: int) -> float:
    """Мелкий текст старых новелл OCR читает плохо: 600 px по высоте -> ×3, 1080 -> ×1.67."""
    if frame_h <= 0:
        return 1.0
    return max(1.0, min(3.0, 1800.0 / frame_h))


def background_is_dark(img: Image.Image) -> bool:
    """Фон занимает большую часть окна диалога, поэтому медиана яркости = яркость фона."""
    gray = img.convert("L")
    return ImageStat.Stat(gray).median[0] < 110


def prepare(frame: Image.Image, region: Rect, *, binarize: bool = False) -> Prepared:
    crop_img, crop = crop_norm(frame, region)
    scale = upscale_factor(frame.height)
    if scale > 1.01:
        crop_img = crop_img.resize(
            (round(crop_img.width * scale), round(crop_img.height * scale)), Image.Resampling.LANCZOS
        )
    gray = crop_img.convert("L")
    if background_is_dark(gray):
        gray = ImageOps.invert(gray)  # тёмный текст на светлом читается стабильнее
    gray = ImageOps.autocontrast(gray, cutoff=1)
    if binarize:
        gray = gray.point(lambda p: 255 if p > 140 else 0)
    return Prepared(gray, scale, crop, frame.width, frame.height)


def thumbnail(frame: Image.Image, region: Rect, max_w: int = 320) -> Image.Image:
    """Серый слепок области: по нему понимаем, сменился ли текст.

    Не слишком мелкий: при сильном уменьшении буквы размываются в фон, и новая
    реплика выглядит как старая.
    """
    crop_img, _ = crop_norm(frame, region)
    scale = min(1.0, max_w / max(1, crop_img.width))
    size = (max(1, round(crop_img.width * scale)), max(1, round(crop_img.height * scale)))
    return crop_img.convert("L").resize(size, Image.Resampling.BOX)


def thumb_diff(a: Image.Image, b: Image.Image, level: int = 28) -> float:
    """Доля пикселей (0..1), яркость которых заметно поменялась.

    Доля, а не средняя разница: пара новых слов на большом окне диалога почти не
    двигает среднее, но даёт сотни изменившихся пикселей.
    """
    if a.size != b.size:
        b = b.resize(a.size)
    diff = ImageChops.difference(a, b).point(lambda p: 255 if p > level else 0)
    hist = diff.histogram()
    total = a.width * a.height
    return hist[255] / total if total else 0.0


def changed(a: Image.Image, b: Image.Image, threshold: float = 0.004) -> bool:
    return thumb_diff(a, b) > threshold


def is_black(frame: Image.Image) -> bool:
    """Захват вернул пустой кадр (DRM, старый DirectX, свёрнутое окно)."""
    lo, hi = frame.convert("L").getextrema()
    return hi < 10
