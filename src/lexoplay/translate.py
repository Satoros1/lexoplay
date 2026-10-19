"""Перевод реплики.

Основной путь: сервер Lexo (/desktop/translate), там кеш, лимит и языки из профиля.
Запасной путь без привязки: Claude Haiku напрямую своим ключом Anthropic.

Напрямую переводим быстро:
* thinking выключен: Haiku 5.x по умолчанию сначала думает, это лишние секунды и
  токены, а при маленьком max_tokens ответ обрывался до текста;
* модель отвечает просто переводом, без JSON, поэтому разбирать нечего;
* ответ идёт потоком: первые слова видны в строке, пока модель дописывает остальное;
* соединение с сервером открывается заранее, пока идёт распознавание.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

log = logging.getLogger(__name__)

LANGS = {
    "en": "English",
    "ru": "Russian",
    "uk": "Ukrainian",
    "nl": "Dutch",
    "de": "German",
    "es": "Spanish",
    "fr": "French",
    "it": "Italian",
    "pt": "Portuguese",
    "pl": "Polish",
}

SYSTEM_PROMPT = """You translate dialogue from a video game into {target}.
Game: {game}. Keep the speaker's tone, slang and politeness level.
The line was read from the screen by OCR. It may contain recognition errors (l/I, 0/O, rn/m,
missing spaces, stray symbols) and may be in ALL CAPS because of a pixel font: read it the way
the game meant it and use normal sentence case in the translation.
Earlier lines are only context. Translate only the line after "Translate:".
Reply with the translation and nothing else: no quotes, no notes, no original text, no speaker name.
Use the glossary for names and terms.
Never use the em dash character; use a comma, a colon or a new sentence instead."""

MAX_TOKENS = 1024


class TranslationError(Exception):
    """Сеть, ключ, лимиты, кривой ответ. message можно показывать игроку.

    code различает случаи, на которые приложение реагирует по-разному:
    limit_reached, device_revoked/unauthorized, update_required, offline.
    """

    def __init__(self, message: str, code: str = "error") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ContextLine:
    speaker: str | None
    source: str
    translation: str


Partial = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class TranslateRequest:
    text: str
    speaker: str | None
    game: str
    target_lang: str = "ru"
    source_lang: str = "en"
    context: tuple[ContextLine, ...] = ()
    glossary: dict[str, str] = field(default_factory=dict)
    line_id: str = ""
    session_id: str = ""
    game_exe: str = ""
    # кусок перевода по мере того, как модель его пишет (из фонового потока)
    on_partial: Partial | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class TranslateResult:
    source: str
    translation: str


class Translator(Protocol):
    async def translate(self, req: TranslateRequest) -> TranslateResult: ...


def lang_name(code: str) -> str:
    return LANGS.get(code, code)


def build_system(req: TranslateRequest) -> str:
    return SYSTEM_PROMPT.format(target=lang_name(req.target_lang), game=req.game or "unknown")


def _speaker(s: str | None) -> str:
    return f"{s}: " if s else ""


def build_user(req: TranslateRequest) -> str:
    parts = []
    if req.glossary:
        parts.append(f"Glossary: {json.dumps(req.glossary, ensure_ascii=False)}")
    if req.context:
        parts.append("Earlier lines (oldest first, original => translation):")
        parts += [f"{_speaker(c.speaker)}{c.source} => {c.translation}" for c in req.context]
    parts.append("Translate:")
    parts.append(f"{_speaker(req.speaker)}{req.text}")
    return "\n".join(parts)


def no_dash(text: str) -> str:
    """Длинное тире в переводе читается как машинный текст: в Lexo его нет нигде."""
    return text.replace(" — ", ", ").replace("—", ", ").strip()


_QUOTES = "\"'«»“”„`"
_LABEL = re.compile(r"^(translation|перевод)\s*:\s*", re.I)


def clean_translation(text: str, speaker: str | None = None, *, partial: bool = False) -> str:
    """Ответ модели → строка для показа: без кавычек, подписи «Перевод:», имени говорящего.

    Если модель по привычке всё же ответила JSON, достаём из него перевод."""
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`").strip()
        if t.lower().startswith("json"):
            t = t[4:].strip()
    if t.startswith("{"):
        if partial:
            return ""  # JSON дописывается: показывать нечего
        try:
            return parse_reply(t, "").translation
        except (ValueError, json.JSONDecodeError):
            return ""
    t = _LABEL.sub("", t)
    if speaker:
        for prefix in (f"{speaker}:", f"{speaker.upper()}:"):
            if t.startswith(prefix):
                t = t[len(prefix) :].lstrip()
    if not partial:
        t = t.strip(_QUOTES).strip()
    else:
        t = t.lstrip(_QUOTES)
    return no_dash(t) if not partial else t.replace("—", ",")


def parse_reply(raw: str, original: str) -> TranslateResult:
    """JSON-ответ {"source", "translation"} (так отвечает сервер Lexo и старые промпты)."""
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        raise ValueError(f"no JSON in reply: {raw[:80]!r}")
    data = json.loads(match.group(0))
    translation = no_dash(str(data.get("translation", "")))
    if not translation:
        raise ValueError("empty translation")
    source = str(data.get("source", "")).strip() or original
    return TranslateResult(source=source, translation=translation)


class AnthropicTranslator:
    """Haiku через официальный SDK, потоком. Ключ и модель берутся из настроек.

    Без temperature: модели Claude 4.7+ и 5.x не принимают параметры сэмплинга.
    thinking выключен; если какая-то модель его выключить не даст, переходим на effort=low.
    """

    def __init__(self, api_key: str, model: str, *, timeout: float = 15.0) -> None:
        import anthropic  # лениво: тесты и самопроверка не тянут SDK без нужды

        self._anthropic = anthropic
        self._client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=1)
        self._model = model
        self._thinking_off = True
        self._last_used = 0.0

    def _params(self, req: TranslateRequest) -> dict:
        params: dict = {
            "model": self._model,
            "max_tokens": MAX_TOKENS,
            "system": build_system(req),
            "messages": [{"role": "user", "content": build_user(req)}],
        }
        if self._thinking_off:
            params["thinking"] = {"type": "disabled"}
        else:
            params["output_config"] = {"effort": "low"}
        return params

    async def warm(self) -> None:
        """Открыть соединение заранее (TLS ~0.1–0.3 с), пока идёт захват и OCR."""
        if time.monotonic() - self._last_used < 20:
            return
        self._last_used = time.monotonic()
        try:
            await self._client.models.list(limit=1)
        except Exception:
            log.debug("прогрев соединения не удался", exc_info=True)

    async def _stream(self, req: TranslateRequest) -> tuple[str, str | None]:
        buf = ""
        shown = ""
        async with self._client.messages.stream(**self._params(req)) as stream:
            async for chunk in stream.text_stream:
                buf += chunk
                if req.on_partial:
                    part = clean_translation(buf, req.speaker, partial=True)
                    if part and part != shown:
                        shown = part
                        try:
                            req.on_partial(part)
                        except Exception:
                            log.debug("on_partial упал", exc_info=True)
            final = await stream.get_final_message()
        self._last_used = time.monotonic()
        return buf, getattr(final, "stop_reason", None)

    async def translate(self, req: TranslateRequest) -> TranslateResult:
        a = self._anthropic
        for _attempt in range(3):
            try:
                raw, stop = await self._stream(req)
            except a.AuthenticationError as exc:
                raise TranslationError("Ключ Anthropic не подходит. Проверь его в настройках.", "bad_key") from exc
            except a.PermissionDeniedError as exc:
                raise TranslationError("У ключа Anthropic нет доступа к этой модели.", "bad_key") from exc
            except a.RateLimitError as exc:
                raise TranslationError("Слишком много запросов. Подожди немного.") from exc
            except a.APITimeoutError as exc:
                raise TranslationError("Не успел перевести, попробуй ещё раз.", "offline") from exc
            except a.APIConnectionError as exc:
                raise TranslationError("Нет связи с сервером перевода.", "offline") from exc
            except a.NotFoundError as exc:
                raise TranslationError(f"Модель {self._model} не найдена. Поменяй её в настройках.") from exc
            except a.BadRequestError as exc:
                if self._thinking_off and "thinking" in str(exc).lower():
                    log.info("модель не даёт выключить thinking, перехожу на effort=low: %s", exc)
                    self._thinking_off = False
                    continue
                log.warning("Anthropic 400: %s", exc)
                raise TranslationError(f"Сервер перевода отклонил запрос: {_short(exc)}") from exc
            except a.APIStatusError as exc:
                raise TranslationError(f"Сервер перевода ответил ошибкой {exc.status_code}.") from exc
            except Exception as exc:  # несовместимый SDK и прочее: не показывать игроку трейсбек
                log.exception("перевод упал")
                raise TranslationError("Перевод не получился, подробности в логе.") from exc
            translation = clean_translation(raw, req.speaker)
            if translation:
                return TranslateResult(source=req.text, translation=translation)
            if stop == "refusal":
                raise TranslationError("Модель отказалась переводить эту реплику.")
            log.warning("пустой перевод: stop_reason=%s, ответ=%r", stop, raw[:200])
        raise TranslationError("Модель не прислала перевод, попробуй ещё раз.")


def _short(exc: Exception) -> str:
    msg = getattr(exc, "message", None) or str(exc)
    return str(msg)[:160]


class LexoTranslator:
    """Перевод через сервер Lexo. Повтор того же line_id сервер не списывает с лимита дважды."""

    def __init__(self, client, on_usage=None) -> None:  # type: ignore[no-untyped-def]
        self._client = client
        self._on_usage = on_usage

    async def warm(self) -> None:
        return None  # httpx держит соединение с сервером Lexo живым сам

    async def translate(self, req: TranslateRequest) -> TranslateResult:
        from lexoplay.lexo import GameRef, LexoError, LexoOffline

        try:
            source, translation, _cached, usage = await self._client.translate(
                session_id=req.session_id,
                line_id=req.line_id,
                game=GameRef(req.game, req.game_exe),
                text=req.text,
                speaker=req.speaker,
                context=[{"speaker": c.speaker, "source": c.source, "translation": c.translation} for c in req.context],
            )
        except LexoOffline as exc:
            msg = "Не успел перевести, попробуй ещё раз." if str(exc) == "timeout" else "Нет связи с Lexo."
            raise TranslationError(msg, "offline") from exc
        except LexoError as exc:
            raise TranslationError(exc.message, exc.code) from exc
        if usage and self._on_usage:
            self._on_usage(usage)
        return TranslateResult(source=source or req.text, translation=no_dash(translation))
