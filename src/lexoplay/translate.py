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

# Версия промпта и обработки перевода. Меняется, когда перевод заметно меняется: кеш
# реплик со старой версией не используется, они переведутся заново уже по-новому.
TRANSLATION_VERSION = 3

SYSTEM_PROMPT = """You are the localizer of the video game "{game}". The player reads its dialogue in {target}: you translate it line by line while they play.

Write the translation a professional {target} game localizer would ship:
- Natural, idiomatic {target} that a native speaker would really say in this situation. Rephrase freely; never translate word for word, never use calques, rare, bookish or made-up words.
- Translate the whole meaning: never drop a word and never put "..." in place of a word you are unsure about.
- Keep the perspective: a line in which the speaker talks about themselves stays in the first person and is never retold ("he says that...", "she asks..."). Never talk to the player, never add the speaker's name.
- A line with a speaker is that character's own words. A line without a speaker is narration, the hero's thoughts, a sign or a menu: translate it as such.
- Keep each character's voice: slang, rudeness, jokes, hesitation, politeness. Stay consistent with the earlier translations: the same form of address between the same characters (ты/вы and the like), the same genders, the same names and terms. The glossary wins over everything. Transliterate names by the usual {target} rules.
- The text comes from OCR and can be garbled: l/I, 0/O, rn/m, split or merged words ("vi ew", "becamewarped"), stray symbols like | or #. Work out the words the game really shows from the letters and the context, then translate them. ALL CAPS is just the game's font: use normal capitalization.
- The screen sometimes shows the same text twice (say, in a box behind the dialogue), so a sentence may come twice: translate it once and never invent a link between the copies.
- If the speaker label looks like a piece of the sentence rather than a name, treat it as part of the line.
- Punctuation: keep ? ! and three dots (...) as in the original, and a period where it has one. If the original ends with a comma or breaks off because the sentence continues in the next text box, end with "..." and never with a comma.
{examples}
Reply with the translation of the line after "Translate this line" and nothing else: no quotes, no notes, no original text, no speaker name.
Never use the em dash character; use a comma, a colon or a new sentence instead."""

# Образцы хорошего перевода: маленькой модели они дают больше, чем любые правила.
EXAMPLES = {
    "ru": [
        ("My view became warped, twisted.", "Всё перед глазами поплыло и исказилось."),
        ("Huh? You got a problem with me, punk?", "Чего? Есть ко мне претензии, сопляк?"),
        ("Don't beat around the bush. Just tell me.", "Не ходи вокруг да около. Просто скажи."),
        ("And then, out of nowhere,", "И тут, откуда ни возьмись..."),
        ("Ugh... my head is killing me.", "Ох... голова просто раскалывается."),
        ("l can't be|ieve this is happening...", "Не могу поверить, что это происходит..."),
        ("I'm sorry to have kept you waiting.", "Простите, что заставил вас ждать."),
    ],
}


def examples_block(target_lang: str) -> str:
    pairs = EXAMPLES.get(target_lang)
    if not pairs:
        return ""
    lines = "\n".join(f"{en} → {tr}" for en, tr in pairs)
    return f"\nExamples of the style we want (original → translation):\n{lines}\n"


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
    previous: str | None = None  # «Перевести иначе»: прошлый перевод, который не понравился
    # кусок перевода по мере того, как модель его пишет (из фонового потока)
    on_partial: Partial | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class TranslateResult:
    source: str
    translation: str
    usage: tuple[int, int] | None = None  # токены своего ключа (вход, выход): для счётчика расходов


class Translator(Protocol):
    async def translate(self, req: TranslateRequest) -> TranslateResult: ...


def lang_name(code: str) -> str:
    return LANGS.get(code, code)


def build_system(req: TranslateRequest) -> str:
    return SYSTEM_PROMPT.format(
        target=lang_name(req.target_lang),
        game=(req.game or "unknown").replace('"', "'"),
        examples=examples_block(req.target_lang),
    )


def _attr(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "'").replace("<", "‹").replace(">", "›")


def _line(speaker: str | None, text: str) -> str:
    who = f' speaker="{_attr(speaker)}"' if speaker else ""
    return f"<line{who}>{text}</line>"


def build_user(req: TranslateRequest) -> str:
    """Глоссарий, прошлые реплики с их переводами (от старых к новым) и сама реплика.

    Прошлые реплики не только для понимания сцены: по ним модель держит те же имена,
    пол персонажей и «ты/вы», что уже звучали в переводе."""
    parts = []
    if req.glossary:
        parts.append("<glossary>")
        parts += [f"{k} => {v}" for k, v in req.glossary.items()]
        parts.append("</glossary>")
    if req.context:
        parts.append("<earlier_lines>")
        for c in req.context:
            parts.append(_line(c.speaker, c.source))
            parts.append(f"<translation>{c.translation}</translation>")
        parts.append("</earlier_lines>")
    if req.previous:
        parts.append(f"<rejected_translation>{req.previous}</rejected_translation>")
        parts.append(
            "The player found this translation of the line inaccurate or unnatural. "
            "Translate the line again from scratch and make it clearly better."
        )
    parts.append("Translate this line:")
    parts.append(_line(req.speaker, req.text))
    return "\n".join(parts)


def no_dash(text: str) -> str:
    """Длинное тире в переводе читается как машинный текст: в Lexo его нет нигде.

    Тире в конце (речь оборвалась) становится многоточием, а не запятой."""
    t = text.strip()
    t = re.sub(r"\s*[—–]\s*([\"'»”’)\]]*)$", r"...\1", t)
    return t.replace(" — ", ", ").replace("—", ", ").strip()


_CLOSERS = "\"'»”’)]"
_MARKS = ".!?…,;:—–-"


def line_ending(text: str) -> str:
    """Чем кончается реплика: «…», «?», «!», «?!», «.», «,» (фраза продолжится), «—» (оборвалась) или ""."""
    t = text.rstrip().rstrip(_CLOSERS).rstrip()
    if t.endswith(("...", "…", "..")):
        return "…"
    m = re.search(r"[.!?,;:—–-]+$", t)
    if not m:
        return ""
    tail = m.group(0)
    if "?" in tail or "!" in tail:
        q = "".join(c for c in tail if c in "?!")
        return "?!" if "?" in q and "!" in q else q[0]
    if tail[-1] in ",;:":
        return ","
    if tail[-1] in "—–-":
        return "—"
    return "."


def fix_punctuation(source: str, translation: str) -> str:
    """Конец перевода как у оригинала: без висячей запятой, с точкой и многоточием там, где они были.

    Модель иногда теряет точку или «...» в конце, а реплику, которая продолжается в следующем
    окне диалога (оригинал кончается запятой), заканчивает запятой. Такое чиним здесь."""
    t = translation.rstrip()
    if not t:
        return t
    i = len(t)
    while i > 0 and (t[i - 1] in _CLOSERS or t[i - 1] in _MARKS or t[i - 1].isspace()):
        i -= 1
    body, trail = t[:i], t[i:]
    marks = "".join(c for c in trail if c in _MARKS)
    closers = "".join(c for c in trail if c in _CLOSERS)
    marks_after = bool(closers) and bool(marks) and trail.rstrip()[-1] in _MARKS  # «Лидер».
    src = line_ending(source)
    cont = "..." if src in ("…", ",", "—", "") else src
    if marks and marks[-1] in ",;:—–-":
        fixed = marks.rstrip(",;:—–- ") or ""
        fixed = fixed if fixed and fixed[-1] in ".!?…" else cont
    elif not marks:
        fixed = {"": "", ",": "...", "—": "...", "…": "..."}.get(src, src)
    elif marks in (".",) and src == "…":
        fixed = "..."
    else:
        return t
    if not fixed:
        return (body + closers) if closers else body
    if marks_after or (closers and not marks and fixed == "."):
        return body + closers + fixed
    return body + fixed + closers


_QUOTES = "\"'«»“”„`"
_LABEL = re.compile(r"^(translation|перевод)\s*:\s*", re.I)


_TAG = re.compile(r"</?(?:translation|line)[^>]*>", re.I)


def clean_translation(text: str, speaker: str | None = None, *, partial: bool = False) -> str:
    """Ответ модели → строка для показа: без кавычек, подписи «Перевод:», имени говорящего.

    Если модель по привычке всё же ответила JSON или обернула ответ в теги, достаём перевод."""
    t = _TAG.sub("", text).strip()
    if partial and t.startswith("<") and ">" not in t:
        return ""  # тег дописывается
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
            # постоянная часть промпта: Claude кеширует её, повторные запросы дешевле и быстрее
            "system": [{"type": "text", "text": build_system(req), "cache_control": {"type": "ephemeral"}}],
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

    async def _stream(self, req: TranslateRequest) -> tuple[str, str | None, tuple[int, int] | None]:
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
        return buf, getattr(final, "stop_reason", None), usage_of(final)

    async def translate(self, req: TranslateRequest) -> TranslateResult:
        a = self._anthropic
        for _attempt in range(3):
            try:
                raw, stop, usage = await self._stream(req)
            except a.BadRequestError as exc:
                if self._thinking_off and "thinking" in str(exc).lower():
                    log.info("модель не даёт выключить thinking, перехожу на effort=low: %s", exc)
                    self._thinking_off = False
                    continue
                raise friendly_error(a, exc, self._model) from exc
            except Exception as exc:
                raise friendly_error(a, exc, self._model) from exc
            translation = fix_punctuation(req.text, clean_translation(raw, req.speaker))
            if translation:
                return TranslateResult(source=req.text, translation=translation, usage=usage)
            if stop == "refusal":
                raise TranslationError("Модель отказалась переводить эту реплику.")
            log.warning("пустой перевод: stop_reason=%s, ответ=%r", stop, raw[:200])
        raise TranslationError("Модель не прислала перевод, попробуй ещё раз.")


def _short(exc: Exception) -> str:
    msg = getattr(exc, "message", None) or str(exc)
    return str(msg)[:160]


def usage_of(message: object) -> tuple[int, int] | None:
    u = getattr(message, "usage", None)
    if u is None:
        return None
    try:
        return int(getattr(u, "input_tokens", 0) or 0), int(getattr(u, "output_tokens", 0) or 0)
    except (TypeError, ValueError):
        return None


def friendly_error(a, exc: BaseException, model: str) -> TranslationError:  # type: ignore[no-untyped-def]
    """Ошибка SDK Anthropic → понятный текст для игрока."""
    if isinstance(exc, TranslationError):
        return exc
    if isinstance(exc, a.AuthenticationError):
        return TranslationError("Ключ Anthropic не подходит. Проверь его во вкладке «Ещё».", "bad_key")
    if isinstance(exc, a.PermissionDeniedError):
        return TranslationError("У ключа Anthropic нет доступа к этой модели.", "bad_key")
    if isinstance(exc, a.RateLimitError):
        return TranslationError("Слишком много запросов. Подожди немного.")
    if isinstance(exc, a.APITimeoutError):
        return TranslationError("Не успел перевести, попробуй ещё раз.", "offline")
    if isinstance(exc, a.APIConnectionError):
        return TranslationError("Нет связи с сервером перевода.", "offline")
    if isinstance(exc, a.NotFoundError):
        return TranslationError(f"Модель {model} не найдена. Поменяй её во вкладке «Ещё».")
    if isinstance(exc, a.BadRequestError):
        text = str(exc).lower()
        if "credit balance" in text or "billing" in text:
            return TranslationError("На счету Anthropic закончились деньги: пополни баланс в console.anthropic.com.", "no_credit")
        log.warning("Anthropic 400: %s", exc)
        return TranslationError(f"Сервер перевода отклонил запрос: {_short(exc)}")
    if isinstance(exc, a.APIStatusError):
        if getattr(exc, "status_code", 0) == 529:
            return TranslationError("Серверы Anthropic перегружены, попробуй через минуту.", "offline")
        return TranslationError(f"Сервер перевода ответил ошибкой {exc.status_code}.")
    log.error("перевод упал", exc_info=exc)  # несовместимый SDK и прочее: не показывать трейсбек
    return TranslationError("Перевод не получился, подробности в логе.")


async def check_key(api_key: str, model: str, *, timeout: float = 15.0) -> str:
    """Проверить ключ и модель крошечным запросом (пара токенов). Ошибка → TranslationError."""
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=0)
    params: dict = {
        "model": model,
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "Reply with: ok"}],
        "thinking": {"type": "disabled"},
    }
    try:
        try:
            await client.messages.create(**params)
        except anthropic.BadRequestError as exc:
            if "thinking" not in str(exc).lower():
                raise
            params.pop("thinking")
            await client.messages.create(**params)
    except Exception as exc:
        raise friendly_error(anthropic, exc, model) from exc
    finally:
        await client.close()
    return model


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
                glossary=req.glossary or None,
                previous=req.previous,
            )
        except LexoOffline as exc:
            msg = "Не успел перевести, попробуй ещё раз." if str(exc) == "timeout" else "Нет связи с Lexo."
            raise TranslationError(msg, "offline") from exc
        except LexoError as exc:
            raise TranslationError(exc.message, exc.code) from exc
        if usage and self._on_usage:
            self._on_usage(usage)
        return TranslateResult(source=source or req.text, translation=fix_punctuation(req.text, no_dash(translation)))
