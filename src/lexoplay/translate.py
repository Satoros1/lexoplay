"""Перевод реплики.

Основной путь: сервер Lexo (/desktop/translate), там тот же промпт, кеш и лимит.
Запасной путь без привязки: Claude Haiku напрямую своим ключом Anthropic.
"""

from __future__ import annotations

import json
import logging
import re
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
The source comes from OCR and may contain recognition errors
(l/I, 0/O, rn/m, missing spaces, stray symbols). Silently fix them before translating.
Use the glossary for names and terms. Do not explain, do not add notes.
Never use the em dash character in the translation; use a comma, a colon or a new sentence instead.
Return only JSON: {{"source": "<corrected {source_lang} text>", "translation": "<translation>"}}"""


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


@dataclass(frozen=True, slots=True)
class TranslateResult:
    source: str
    translation: str


class Translator(Protocol):
    async def translate(self, req: TranslateRequest) -> TranslateResult: ...


def lang_name(code: str) -> str:
    return LANGS.get(code, code)


def build_system(req: TranslateRequest) -> str:
    return SYSTEM_PROMPT.format(
        target=lang_name(req.target_lang), game=req.game or "unknown", source_lang=lang_name(req.source_lang)
    )


def _speaker(s: str | None) -> str:
    return f"{s}: " if s else ""


def build_user(req: TranslateRequest) -> str:
    parts = [f"Glossary: {json.dumps(req.glossary, ensure_ascii=False)}"]
    if req.context:
        parts.append("Previous lines (oldest first):")
        parts += [f"{_speaker(c.speaker)}{c.source} => {c.translation}" for c in req.context]
    parts.append("Translate this line:")
    parts.append(f"{_speaker(req.speaker)}{req.text}")
    return "\n".join(parts)


def no_dash(text: str) -> str:
    """Длинное тире в переводе читается как машинный текст: в Lexo его нет нигде."""
    return text.replace(" \u2014 ", ", ").replace("\u2014", ", ").strip()


def parse_reply(raw: str, original: str) -> TranslateResult:
    """Достаём JSON из ответа модели. Пустой перевод = ошибка, чтобы сработал повтор."""
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
    """Haiku через официальный SDK. Ключ и модель берутся из настроек.

    Без temperature: модели Claude 4.7+ и 5.x не принимают параметры сэмплинга,
    а SDK 1.x убрал их из messages.create совсем.
    """

    def __init__(self, api_key: str, model: str, *, timeout: float = 10.0) -> None:
        import anthropic  # лениво: тесты и самопроверка не тянут SDK без нужды

        self._anthropic = anthropic
        self._client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=1)
        self._model = model

    async def translate(self, req: TranslateRequest) -> TranslateResult:
        a = self._anthropic
        last_err: Exception | None = None
        for _attempt in range(2):  # один повтор, если модель вернула не JSON
            try:
                resp = await self._client.messages.create(
                    model=self._model,
                    max_tokens=400,
                    system=build_system(req),
                    messages=[{"role": "user", "content": build_user(req)}],
                )
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
                log.warning("Anthropic 400: %s", exc)
                raise TranslationError(f"Сервер перевода отклонил запрос: {_short(exc)}") from exc
            except a.APIStatusError as exc:
                raise TranslationError(f"Сервер перевода ответил ошибкой {exc.status_code}.") from exc
            except Exception as exc:  # несовместимый SDK и прочее: не показывать игроку трейсбек
                log.exception("перевод упал")
                raise TranslationError("Перевод не получился, подробности в логе.") from exc
            raw = "".join(getattr(b, "text", "") or "" for b in resp.content)
            try:
                return parse_reply(raw, req.text)
            except (ValueError, json.JSONDecodeError) as exc:
                last_err = exc
        raise TranslationError("Модель ответила непонятно, попробуй ещё раз.") from last_err


def _short(exc: Exception) -> str:
    msg = getattr(exc, "message", None) or str(exc)
    return str(msg)[:160]


class LexoTranslator:
    """Перевод через сервер Lexo. Повтор того же line_id сервер не списывает с лимита дважды."""

    def __init__(self, client, on_usage=None) -> None:  # type: ignore[no-untyped-def]
        self._client = client
        self._on_usage = on_usage

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
