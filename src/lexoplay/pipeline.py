"""Поток F2: кадр -> OCR -> реплика -> перевод. Без Qt и без Windows: всё внедряется.

Так его можно гонять в тестах на Linux с поддельным захватом, OCR и переводчиком.

Способ подготовки картинки для OCR подбирается сам: сначала тот, что подошёл игре
раньше; если прочитано плохо (много мусора вместо слов), пробуем остальные и берём
лучший. Перевод ищется сначала в памяти сессии, потом в кеше на диске, и только потом
идёт запрос к модели.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

from PIL import Image

from lexoplay.dedupe import LineMemory, LineRecord, normalize_text, similarity
from lexoplay.geometry import Rect
from lexoplay.ocr_model import OcrResult
from lexoplay.ocrfuse import fuse
from lexoplay.ocrscore import Quality, quality
from lexoplay.preprocess import MODES, Prepared, changed, prepare, preview, thumbnail
from lexoplay.textproc import Utterance, has_text, to_utterance
from lexoplay.translate import ContextLine, TranslateRequest, TranslationError, Translator

log = logging.getLogger(__name__)


class OcrEngine(Protocol):
    async def recognize(self, prepared: Prepared) -> OcrResult: ...


class CaptureError(Exception):
    """Кадр не получить: окно закрыто, свёрнуто, захват недоступен."""


Grab = Callable[[], Image.Image]
DebugSink = Callable[[str, object], None]


class Cache(Protocol):
    def find(self, exe: str, lang: str, text: str) -> LineRecord | None: ...
    def add(self, exe: str, lang: str, rec: LineRecord) -> None: ...
    def recent(self, exe: str, lang: str, limit: int) -> list[LineRecord]: ...


@dataclass(slots=True)
class Reading:
    """Что прочитано в окне диалога и каким способом."""

    result: OcrResult
    name_text: str | None
    mode: str
    quality: Quality
    tried: dict[str, str] = field(default_factory=dict)  # способ -> оценка, для лога


@dataclass(slots=True)
class F2Outcome:
    kind: Literal["translated", "repeat", "no_text", "error"]
    record: LineRecord | None = None
    message: str | None = None
    ocr: OcrResult | None = None
    thumb: Image.Image | None = None  # слепок области: по нему поймём, что реплика сменилась
    timings: dict[str, float] = field(default_factory=dict)
    code: str | None = None  # для error: limit_reached, device_revoked, offline…
    utterance: Utterance | None = None
    cached: bool = False  # перевод из кеша на диске, без запроса
    ocr_mode: str | None = None  # каким способом прочитано
    quality: Quality | None = None
    preview: Image.Image | None = None  # цветная обрезка окна диалога для окна LexoPlay
    usage: tuple[int, int] | None = None  # токены своего ключа: (вход, выход)


@dataclass(slots=True)
class ScanOutcome:
    """F3 без перевода: только текст и рамки слов (и перевод, если реплику уже переводили)."""

    ok: bool
    ocr: OcrResult | None = None
    utterance: Utterance | None = None
    record: LineRecord | None = None
    thumb: Image.Image | None = None
    message: str | None = None
    ocr_mode: str | None = None
    quality: Quality | None = None
    preview: Image.Image | None = None


@dataclass(frozen=True, slots=True)
class Target:
    game_title: str
    region: Rect
    name_region: Rect | None = None
    ocr_mode: str | None = None  # None: подобрать
    game_exe: str = ""
    session_id: str = ""
    glossary: dict[str, str] = field(default_factory=dict, hash=False)


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
        stable_wait: float = 0.1,
        max_wait: float = 1.5,
        stable_threshold: float = 0.001,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        run_sync: Callable[[Grab], Awaitable[Image.Image]] | None = None,
        debug: DebugSink | None = None,
        cache: Cache | None = None,
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
        self.cache = cache
        self._warm_task: asyncio.Future | None = None

    async def _grab(self, grab: Grab) -> Image.Image:
        try:
            return await self._run_sync(grab)
        except CaptureError:
            raise
        except Exception as exc:  # GDI/WinRT бросают что угодно
            raise CaptureError(str(exc)) from exc

    async def stable_frame(
        self, grab: Grab, region: Rect, max_wait: float | None = None, mode: str | None = None
    ) -> Image.Image:
        """Ждём, пока текст допечатается.

        Обычно хватает двух одинаковых кадров подряд. Но если за текстом шевелится сцена
        (Danganronpa, анимированные фоны), одинаковых кадров не будет никогда, и раньше
        перевод брался с недопечатанной реплики. Тогда сравниваем уже текст: читаем OCR,
        пока он не перестанет расти."""
        wait = max_wait or self._max_wait
        frame = await self._grab(grab)
        prev = thumbnail(frame, region)
        steps = max(1, int(min(wait, 0.6) / self._stable_wait))
        for _ in range(steps):
            await self._sleep(self._stable_wait)
            nxt = await self._grab(grab)
            cur = thumbnail(nxt, region)
            frame = nxt
            if not changed(prev, cur, self._stable_threshold):
                return frame
            prev = cur
        # сцена шевелится: ждём, пока перестанет меняться сам текст
        # Текст «готов», когда перестал расти. Сравниваем мягко: пиксельный шрифт, который
        # «дышит» (Hotline Miami), OCR читает чуть по-разному от кадра к кадру.
        read_mode = mode if mode in MODES else MODES[0]
        last = normalize_text((await self._read(frame, region, read_mode)).raw_text)
        for _ in range(max(1, int(wait * 1.5 / 0.3))):
            await self._sleep(0.3)
            nxt = await self._grab(grab)
            now = normalize_text((await self._read(nxt, region, read_mode)).raw_text)
            frame = nxt
            if now and len(now) <= len(last) + 3 and similarity(now, last) >= 0.8:
                return frame
            last = now
        return frame

    async def _read(self, frame: Image.Image, region: Rect, mode: str) -> OcrResult:
        prepared = await asyncio.to_thread(prepare, frame, region, mode=mode)
        if self._debug:
            self._debug(f"prepared_{mode}", prepared.image)
        return await self._ocr.recognize(prepared)

    async def recognize(self, frame: Image.Image, target: Target, *, explore: bool = True) -> Reading:
        """Распознать окно диалога.

        explore=True (F2, F3): читаем всеми способами сразу, берём лучший и добавляем слова,
        которые он пропустил (выделенные другим цветом). explore=False (проверки смены
        реплики): только способом, который подошёл игре."""
        first = target.ocr_mode if target.ocr_mode in MODES else MODES[0]
        if not explore:
            result = await self._read(frame, target.region, first)
            reading = Reading(result, None, first, quality(result, self.source_lang))
        else:
            modes = [first] + [m for m in MODES if m != first]
            got = await asyncio.gather(*(self._read(frame, target.region, m) for m in modes), return_exceptions=True)
            readings = []
            for mode, res in zip(modes, got, strict=True):
                if isinstance(res, BaseException):
                    log.warning("OCR способом %s не прочитал: %s", mode, res)
                    continue
                readings.append((mode, res, quality(res, self.source_lang)))
            if not readings:
                raise got[0] if isinstance(got[0], BaseException) else RuntimeError("OCR не прочитал")
            # лучший по оценке; при равенстве тот, что уже подошёл игре
            mode, best, q = max(readings, key=lambda r: (r[2].rank, r[0] == first))
            others = [r[1] for r in sorted(readings, key=lambda r: -r[2].rank) if r[0] != mode]
            fused, added = fuse(best, others, self.source_lang)
            reading = Reading(fused, None, mode, quality(fused, self.source_lang) if added else q)
            reading.tried = {m: str(qq) for m, _r, qq in readings}
            if added:
                reading.tried["+слов"] = str(added)
            if mode != first or added:
                log.info("OCR: способы %s, выбран %s, добавлено слов из других: %s", reading.tried, mode, added)
        if target.name_region is not None:
            name_res = await self._read(frame, target.name_region, reading.mode)
            reading.name_text = name_res.raw_text
        return reading

    def context_for(self, target: Target, text: str) -> list[LineRecord]:
        """Прошлые реплики для перевода: из этой сессии, а в её начале ещё и из истории игры.

        Без них модель не знает, кто с кем говорит: путает пол, «ты/вы», имена. После
        перезапуска игры память сессии пустая, поэтому добираем последние реплики с диска."""
        ctx = self.memory.context(self._context_size)
        if len(ctx) >= self._context_size or self.cache is None or not target.game_exe:
            return ctx
        try:
            older = self.cache.recent(target.game_exe, self.target_lang, self._context_size)
        except Exception:
            log.exception("история игры недоступна")
            return ctx
        skip = {r.norm for r in ctx} | {normalize_text(text)}
        extra = [r for r in older if r.norm not in skip]
        return (extra + ctx)[-self._context_size :]

    async def run(
        self,
        grab: Grab,
        target: Target,
        on_partial: Callable[[str], None] | None = None,
        *,
        settle: float | None = None,
    ) -> F2Outcome:
        """settle: сколько ждать, пока текст допечатается (автоперевод ждёт дольше)."""
        t0 = time.perf_counter()
        # соединение с сервером перевода открываем сразу, пока идут захват и OCR
        warm = getattr(self.translator, "warm", None)
        self._warm_task = asyncio.ensure_future(warm()) if warm is not None else None
        timings: dict[str, float] = {}
        try:
            frame = await self.stable_frame(grab, target.region, settle, target.ocr_mode)
        except CaptureError as exc:
            return F2Outcome("error", message=f"Не получилось захватить окно игры ({exc}).")
        timings["capture"] = time.perf_counter() - t0
        if self._debug:
            self._debug("frame", frame)

        t1 = time.perf_counter()
        reading = await self.recognize(frame, target)
        result = reading.result
        timings["ocr"] = time.perf_counter() - t1
        thumb = thumbnail(frame, target.region)
        utt = to_utterance(result, reading.name_text)
        seen = {
            "ocr": result,
            "thumb": thumb,
            "timings": timings,
            "utterance": utt,
            "ocr_mode": reading.mode,
            "quality": reading.quality,
            "preview": preview(frame, target.region),
        }
        if self._debug:
            self._debug(
                "ocr",
                {"raw": result.raw_text, "speaker": utt.speaker, "text": utt.text, "mode": reading.mode, "tried": reading.tried},
            )
        if not has_text(utt.text):
            return F2Outcome("no_text", **seen)

        known = self.memory.find(utt.text)
        if known is not None:
            return F2Outcome("repeat", record=known, **seen)
        if self.cache is not None and target.game_exe:
            try:
                stored = self.cache.find(target.game_exe, self.target_lang, utt.text)
            except Exception:
                log.exception("кеш переводов недоступен")
                stored = None
            if stored is not None:
                self.memory.add(stored)  # и в контекст следующих реплик
                return F2Outcome("repeat", record=stored, cached=True, **seen)

        line_id = str(uuid.uuid4())

        req = TranslateRequest(
            text=utt.text,
            speaker=utt.speaker,
            game=target.game_title,
            target_lang=self.target_lang,
            source_lang=self.source_lang,
            context=tuple(ContextLine(r.speaker, r.source, r.translation) for r in self.context_for(target, utt.text)),
            line_id=line_id,
            session_id=target.session_id,
            game_exe=target.game_exe,
            glossary=dict(target.glossary),
            on_partial=on_partial,
        )
        t2 = time.perf_counter()
        warm_task = getattr(self, "_warm_task", None)
        if warm_task is not None and not warm_task.done():
            # соединение почти открыто: чуть подождать дешевле, чем открывать второе
            await asyncio.wait({warm_task}, timeout=0.15)
        try:
            res = await self.translator.translate(req)
        except TranslationError as exc:
            return F2Outcome("error", message=str(exc), code=getattr(exc, "code", "error"), **seen)
        except Exception:  # чужая ошибка не должна показываться игроку трейсбеком
            log.exception("переводчик упал")
            return F2Outcome("error", message="Перевод не получился, подробности в логе.", code="error", **seen)
        timings["translate"] = time.perf_counter() - t2
        rec = LineRecord(
            line_id=line_id,
            speaker=utt.speaker,
            source=res.source,
            translation=res.translation,
            norm=normalize_text(utt.text),
        )
        self.memory.add(rec)
        if self.cache is not None and target.game_exe:
            self.cache.add(target.game_exe, self.target_lang, rec)
        timings["total"] = time.perf_counter() - t0
        return F2Outcome("translated", record=rec, usage=getattr(res, "usage", None), **seen)

    async def scan(self, grab: Grab, target: Target) -> ScanOutcome:
        """Для F3: дождаться стабильного кадра и распознать, без запроса перевода."""
        try:
            frame = await self.stable_frame(grab, target.region, None, target.ocr_mode)
        except CaptureError as exc:
            return ScanOutcome(False, message=f"Не получилось захватить окно игры ({exc}).")
        if self._debug:
            self._debug("frame", frame)
        reading = await self.recognize(frame, target)
        result = reading.result
        utt = to_utterance(result, reading.name_text)
        thumb = thumbnail(frame, target.region)
        extra = {"ocr_mode": reading.mode, "quality": reading.quality, "preview": preview(frame, target.region)}
        if not has_text(utt.text) or not result.words:
            return ScanOutcome(False, ocr=result, utterance=utt, thumb=thumb, **extra)
        record = self.memory.find(utt.text)
        if record is None and self.cache is not None and target.game_exe:
            record = self.cache.find(target.game_exe, self.target_lang, utt.text)
        return ScanOutcome(True, ocr=result, utterance=utt, record=record, thumb=thumb, **extra)
