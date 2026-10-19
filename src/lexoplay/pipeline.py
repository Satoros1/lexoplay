"""Поток F2: кадр -> OCR -> реплика -> перевод. Без Qt и без Windows: всё внедряется.

Так его можно гонять в тестах на Linux с поддельным захватом, OCR и переводчиком.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

from PIL import Image

from lexoplay.dedupe import LineMemory, LineRecord, normalize_text
from lexoplay.geometry import Rect
from lexoplay.ocr_model import OcrResult
from lexoplay.preprocess import Prepared, changed, prepare, thumbnail
from lexoplay.textproc import has_text, to_utterance
from lexoplay.translate import ContextLine, TranslateRequest, TranslationError, Translator


class OcrEngine(Protocol):
    async def recognize(self, prepared: Prepared) -> OcrResult: ...


class CaptureError(Exception):
    """Кадр не получить: окно закрыто, свёрнуто, захват недоступен."""


Grab = Callable[[], Image.Image]
DebugSink = Callable[[str, object], None]


@dataclass(slots=True)
class F2Outcome:
    kind: Literal["translated", "repeat", "no_text", "error"]
    record: LineRecord | None = None
    message: str | None = None
    ocr: OcrResult | None = None
    thumb: Image.Image | None = None  # слепок области: по нему поймём, что реплика сменилась
    timings: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Target:
    game_title: str
    region: Rect
    name_region: Rect | None = None
    binarize: bool = False


class Pipeline:
    def __init__(
        self,
        ocr: OcrEngine,
        translator: Translator,
        memory: LineMemory,
        *,
        target_lang: str = "ru",
        source_lang: str = "en",
        context_size: int = 8,
        stable_wait: float = 0.15,
        max_wait: float = 1.5,
        stable_threshold: float = 0.001,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        run_sync: Callable[[Grab], Awaitable[Image.Image]] | None = None,
        debug: DebugSink | None = None,
    ) -> None:
        self._ocr = ocr
        self.translator = translator
        self.memory = memory
        self.target_lang = target_lang
        self.source_lang = source_lang
        self._context_size = context_size
        self._stable_wait = stable_wait
        self._max_wait = max_wait
        self._stable_threshold = stable_threshold
        self._sleep = sleep
        self._run_sync = run_sync or (lambda fn: asyncio.to_thread(fn))
        self._debug = debug

    async def _grab(self, grab: Grab) -> Image.Image:
        try:
            return await self._run_sync(grab)
        except CaptureError:
            raise
        except Exception as exc:  # GDI/WinRT бросают что угодно
            raise CaptureError(str(exc)) from exc

    async def stable_frame(self, grab: Grab, region: Rect) -> Image.Image:
        """Ждём, пока текст допечатается: два кадра подряд с одинаковой областью."""
        frame = await self._grab(grab)
        prev = thumbnail(frame, region)
        steps = max(1, int(self._max_wait / self._stable_wait))
        for _ in range(steps):
            await self._sleep(self._stable_wait)
            nxt = await self._grab(grab)
            cur = thumbnail(nxt, region)
            frame = nxt
            if not changed(prev, cur, self._stable_threshold):
                return frame
            prev = cur
        return frame

    async def recognize(self, frame: Image.Image, target: Target) -> tuple[OcrResult, str | None]:
        prepared = prepare(frame, target.region, binarize=target.binarize)
        if self._debug:
            self._debug("prepared", prepared.image)
        result = await self._ocr.recognize(prepared)
        name_text: str | None = None
        if target.name_region is not None:
            name_res = await self._ocr.recognize(prepare(frame, target.name_region, binarize=target.binarize))
            name_text = name_res.raw_text
        return result, name_text

    async def run(self, grab: Grab, target: Target) -> F2Outcome:
        t0 = time.perf_counter()
        timings: dict[str, float] = {}
        try:
            frame = await self.stable_frame(grab, target.region)
        except CaptureError as exc:
            return F2Outcome("error", message=f"Не получилось захватить окно игры ({exc}).")
        timings["capture"] = time.perf_counter() - t0
        if self._debug:
            self._debug("frame", frame)

        t1 = time.perf_counter()
        result, name_text = await self.recognize(frame, target)
        timings["ocr"] = time.perf_counter() - t1
        thumb = thumbnail(frame, target.region)
        utt = to_utterance(result, name_text)
        if self._debug:
            self._debug("ocr", {"raw": result.raw_text, "speaker": utt.speaker, "text": utt.text})
        if not has_text(utt.text):
            return F2Outcome("no_text", ocr=result, thumb=thumb, timings=timings)

        known = self.memory.find(utt.text)
        if known is not None:
            return F2Outcome("repeat", record=known, ocr=result, thumb=thumb, timings=timings)

        req = TranslateRequest(
            text=utt.text,
            speaker=utt.speaker,
            game=target.game_title,
            target_lang=self.target_lang,
            source_lang=self.source_lang,
            context=tuple(
                ContextLine(r.speaker, r.source, r.translation) for r in self.memory.context(self._context_size)
            ),
        )
        t2 = time.perf_counter()
        try:
            res = await self.translator.translate(req)
        except TranslationError as exc:
            return F2Outcome("error", message=str(exc), ocr=result, thumb=thumb, timings=timings)
        timings["translate"] = time.perf_counter() - t2
        rec = LineRecord(
            line_id=str(uuid.uuid4()),
            speaker=utt.speaker,
            source=res.source,
            translation=res.translation,
            norm=normalize_text(utt.text),
        )
        self.memory.add(rec)
        timings["total"] = time.perf_counter() - t0
        return F2Outcome("translated", record=rec, ocr=result, thumb=thumb, timings=timings)
