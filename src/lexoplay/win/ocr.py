"""Windows.Media.Ocr через pywinrt.

ВАЖНО: вызывать только из фонового потока BackgroundLoop. Первый импорт winrt
инициализирует COM-апартамент текущего потока как MTA; в потоке Qt (STA) это конфликт.
"""

from __future__ import annotations

import array
import logging

from PIL import Image

from lexoplay.ocr_model import Line, OcrResult, Word
from lexoplay.preprocess import Prepared

log = logging.getLogger(__name__)

INSTALL_HINT = "Add-WindowsCapability -Online -Name 'Language.OCR~~~en-US~0.0.1.0'"


class OcrUnavailable(RuntimeError):
    """В Windows нет английского OCR-пакета."""


def available_languages() -> list[str]:
    from winrt.windows.media.ocr import OcrEngine

    return [lang.language_tag for lang in OcrEngine.available_recognizer_languages]


def _make_buffer(data: bytes):  # type: ignore[no-untyped-def]
    from winrt.windows.storage.streams import DataWriter

    writer = DataWriter()
    try:
        writer.write_bytes(data)
    except TypeError:  # старые сборки pywinrt хотят массив/список
        writer.write_bytes(array.array("B", data))
    return writer.detach_buffer()


class WinOcr:
    def __init__(self, lang: str = "en-US") -> None:
        self._lang_tag = lang
        self._engine = None
        self.language_used: str | None = None

    def _ensure(self):  # type: ignore[no-untyped-def]
        if self._engine is not None:
            return self._engine
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine

        lang = Language(self._lang_tag)
        if not OcrEngine.is_language_supported(lang):
            prefix = self._lang_tag.split("-")[0].lower()
            lang = next(
                (lg for lg in OcrEngine.available_recognizer_languages if lg.language_tag.lower().startswith(prefix)),
                None,
            )
            if lang is None:
                raise OcrUnavailable(f"нет OCR-пакета для {self._lang_tag}")
        engine = OcrEngine.try_create_from_language(lang)
        if engine is None:
            raise OcrUnavailable(f"OcrEngine не создался для {lang.language_tag}")
        self._engine = engine
        self.language_used = lang.language_tag
        log.info("OCR: %s, max %s px", lang.language_tag, OcrEngine.max_image_dimension)
        return engine

    def check(self) -> str:
        """Бросает OcrUnavailable, если OCR не работает. Возвращает язык."""
        self._ensure()
        return self.language_used or self._lang_tag

    async def recognize_image(self, img: Image.Image) -> list[tuple[str, list[tuple[str, float, float, float, float]]]]:
        """Сырые строки: [(текст строки, [(слово, x, y, w, h), ...]), ...] в пикселях img."""
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.media.ocr import OcrEngine

        engine = self._ensure()
        limit = int(OcrEngine.max_image_dimension)
        scale = 1.0
        if max(img.width, img.height) > limit:
            scale = limit / max(img.width, img.height)
            img = img.resize((int(img.width * scale), int(img.height * scale)), Image.Resampling.LANCZOS)
        rgba = img.convert("RGBA")
        bitmap = SoftwareBitmap.create_copy_from_buffer(
            _make_buffer(rgba.tobytes()), BitmapPixelFormat.RGBA8, rgba.width, rgba.height
        )
        result = await engine.recognize_async(bitmap)
        out = []
        for line in result.lines:
            words = []
            for w in line.words:
                r = w.bounding_rect
                words.append((w.text, r.x / scale, r.y / scale, r.width / scale, r.height / scale))
            out.append((line.text, words))
        return out

    async def recognize(self, prepared: Prepared) -> OcrResult:
        raw = await self.recognize_image(prepared.image)
        lines = []
        for _text, words in raw:
            lines.append(Line(tuple(Word(t, prepared.to_norm(x, y, w, h)) for t, x, y, w, h in words)))
        return OcrResult(tuple(lines))
