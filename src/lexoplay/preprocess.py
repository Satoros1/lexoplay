"""Картинка перед OCR, сравнение кадров и проверка «чёрного» захвата. Только Pillow."""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat

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


# Способы подготовки картинки. LexoPlay пробует их сам и запоминает лучший для игры:
# normal  обычный текст: мягкое увеличение, яркость как видит глаз;
# pixel   пиксельный шрифт: увеличение «по пикселям», сглаживание и чёрно-белая картинка;
# color   цветные буквы на тёмном фоне: яркость по самому яркому каналу (синий и красный
#         текст в обычной яркости почти чёрный и сливается с фоном).
MODES = ("normal", "pixel", "color")
MODE_NAMES = {"normal": "обычный", "pixel": "пиксельный шрифт", "color": "цветной текст"}


def value_gray(img: Image.Image) -> Image.Image:
    """Серый по самому яркому каналу (V из HSV): цветной текст остаётся ярким."""
    r, g, b = img.convert("RGB").split()
    return ImageChops.lighter(ImageChops.lighter(r, g), b)


def otsu_level(gray: Image.Image) -> int:
    """Порог Оцу: делит гистограмму на «текст» и «фон» с наибольшей разницей между ними."""
    hist = gray.histogram()[:256]
    total = sum(hist)
    if not total:
        return 128
    sum_all = sum(i * h for i, h in enumerate(hist))
    sum_b = 0.0
    w_b = 0
    best, level = -1.0, 128
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        m_b = sum_b / w_b
        m_f = (sum_all - sum_b) / w_f
        between = w_b * w_f * (m_b - m_f) ** 2
        if between > best:
            best, level = between, t
    return level


def _pixel_scale(frame_h: int) -> int:
    """Целый множитель: при целом увеличении «по пикселям» буквы не плывут."""
    return max(2, min(4, round(upscale_factor(frame_h) + 0.5)))


def prepare(frame: Image.Image, region: Rect, *, mode: str = "normal", binarize: bool = False) -> Prepared:
    if binarize and mode == "normal":
        mode = "pixel"  # профили игр из 0.1–0.3
    crop_img, crop = crop_norm(frame, region)
    if mode == "pixel":
        k = _pixel_scale(frame.height)
        big = crop_img.resize((crop_img.width * k, crop_img.height * k), Image.Resampling.NEAREST)
        gray = value_gray(big)
        if background_is_dark(gray):
            gray = ImageOps.invert(gray)
        # ступеньки пикселей сглаживаем: OCR учили на гладких шрифтах
        gray = gray.filter(ImageFilter.GaussianBlur(k * 0.35))
        level = otsu_level(gray)
        gray = gray.point(lambda p: 255 if p > level else 0)
        return Prepared(gray, float(k), crop, frame.width, frame.height)
    scale = upscale_factor(frame.height)
    if scale > 1.01:
        crop_img = crop_img.resize(
            (round(crop_img.width * scale), round(crop_img.height * scale)), Image.Resampling.LANCZOS
        )
    gray = value_gray(crop_img) if mode == "color" else crop_img.convert("L")
    if background_is_dark(gray):
        gray = ImageOps.invert(gray)  # тёмный текст на светлом читается стабильнее
    gray = ImageOps.autocontrast(gray, cutoff=1)
    return Prepared(gray, scale, crop, frame.width, frame.height)


def preview(frame: Image.Image, region: Rect, max_w: int = 520) -> Image.Image:
    """Цветная обрезка окна диалога для окна LexoPlay: что именно читалось."""
    crop_img, _ = crop_norm(frame, region)
    scale = min(1.0, max_w / max(1, crop_img.width))
    size = (max(1, round(crop_img.width * scale)), max(1, round(crop_img.height * scale)))
    return crop_img.convert("RGB").resize(size, Image.Resampling.LANCZOS)


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
