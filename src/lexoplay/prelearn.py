"""Слова новеллы и модель (своим ключом Anthropic): уровень слов и значения для колоды.

В модель уходят только сами слова и название игры, без текста новеллы. Уровень: A1–C2 по CEFR, а имена
и не-слова помечаются, чтобы не попасть в список. Примеры к словам модель придумывает сама, обычные
и нейтральные: ни героев, ни мест, ни событий игры (игрок её ещё не читал).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from lexoplay.translate import TranslationError, friendly_error, lang_name, no_dash, usage_of

log = logging.getLogger(__name__)
T = TypeVar("T")
R = TypeVar("R")

PROMPT = """A learner whose native language is {tgt} is about to read the visual novel "{game}" in {src}. Before it they learn words that come up in it often:
{words}
For each word, in the same order, reply with one JSON object per line and nothing else:
{{"word": "<the word as given>", "lemma": "<its dictionary form>", "pos": "<part of speech in English: noun, verb, adj, adv...>", "ipa": "<IPA of the lemma, without slashes>", "meaning": "<1 to 3 short meanings in {tgt}, the most likely ones in a story like this, separated by commas>", "example": "<a short natural {src} sentence of your own, 6 to 14 words, with the word in that meaning>", "example_tr": "<that sentence in {tgt}>"}}
The examples must be everyday sentences that have nothing to do with the game: no characters, places, events or plot of "{game}", the learner has not read it yet. No em dash characters."""
MAX_TOKENS = 4000
TIMEOUT_S = 90.0
MEANING_BATCH = 20  # слов за запрос значений
LEVEL_BATCH = 300  # слов за запрос уровней
PARALLEL = 3

LEVEL_PROMPT = """These English words and phrasal verbs come from the visual novel "{game}". For a learner of English, give each one the CEFR level of its most common meaning: A1, A2, B1, B2, C1 or C2.
Mark as NAME anything that is a name in this story: characters, nicknames, places, brands, honorific forms. Mark as X anything that is not a real English word: sounds, interjections, typos, words of other languages.
A phrasal verb whose meaning is just the sum of its words (sit down, go back) is A2. A word that is the second half of a phrase is still rated as a word.
Reply with one line per item, in the same order, as item|LEVEL, nothing else:
{items}"""
_LEVEL_LINE = re.compile(r"^\s*(?:\d+[.)]\s*)?([A-Za-z][A-Za-z' -]*?)\s*[|:\-–]\s*(A1|A2|B1|B2|C1|C2|NAME|X)\b", re.I)


@dataclass(frozen=True, slots=True)
class Prelearned:
    word: str
    lemma: str
    pos: str
    ipa: str
    meaning: str
    example: str
    example_tr: str


def build(game: str, words: list[str], source_lang: str = "en", target_lang: str = "ru") -> str:
    return PROMPT.format(
        tgt=lang_name(target_lang),
        src=lang_name(source_lang),
        game=(game or "unknown").replace('"', "'")[:120],
        words="\n".join(w.strip()[:60] for w in words),
    )


def parse(raw: str, words: list[str]) -> list[Prelearned]:
    """Ответ модели: объект на строку. Слово без значения пропускаем, чужие слова тоже."""
    wanted = {w.lower() for w in words}
    out: dict[str, Prelearned] = {}
    for m in re.finditer(r"\{[^{}]*\}", raw):
        try:
            data = json.loads(m.group(0))
        except ValueError:
            continue
        word = str(data.get("word") or "").strip().lower()
        meaning = no_dash(str(data.get("meaning") or "").strip())
        if word not in wanted or not meaning or word in out:
            continue
        out[word] = Prelearned(
            word=word,
            lemma=str(data.get("lemma") or word).strip()[:80] or word,
            pos=str(data.get("pos") or "").strip()[:20],
            ipa=str(data.get("ipa") or "").strip().strip("/[]")[:60],
            meaning=meaning[:200],
            example=no_dash(str(data.get("example") or "").strip())[:300],
            example_tr=no_dash(str(data.get("example_tr") or "").strip())[:300],
        )
    return [out[w.lower()] for w in words if w.lower() in out]


def build_levels(game: str, items: list[str]) -> str:
    return LEVEL_PROMPT.format(game=(game or "unknown").replace('"', "'")[:120], items="\n".join(i[:60] for i in items))


def parse_levels(raw: str, items: list[str]) -> dict[str, str]:
    wanted = {i.lower() for i in items}
    out: dict[str, str] = {}
    for line in raw.splitlines():
        m = _LEVEL_LINE.match(line)
        if m:
            item = re.sub(r"\s+", " ", m.group(1).strip().lower())
            if item in wanted:
                out[item] = m.group(2).upper()
    return out


async def _ask(tr: object, prompt: str, what: str) -> tuple[str, tuple[int, int] | None]:
    """Один запрос без раздумий: ответ простой, а слов много."""
    a = tr._anthropic  # type: ignore[attr-defined]
    client = tr._client  # type: ignore[attr-defined]
    model = tr._model  # type: ignore[attr-defined]
    params: dict = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
        "thinking": {"type": "disabled"},
    }
    for _attempt in range(2):
        try:
            msg = await client.messages.create(**params, timeout=TIMEOUT_S)
            break
        except a.BadRequestError as exc:
            if "thinking" in params and "thinking" in str(exc).lower():
                params.pop("thinking")
                params["output_config"] = {"effort": "low"}
                continue
            raise friendly_error(a, exc, model) from exc
        except Exception as exc:
            raise friendly_error(a, exc, model) from exc
    else:
        raise TranslationError(f"Модель не приняла запрос ({what}).")
    raw = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text")
    return raw, usage_of(msg)


async def prelearn(
    tr: object, game: str, words: list[str], *, source_lang: str = "en", target_lang: str = "ru"
) -> tuple[list[Prelearned], tuple[int, int] | None]:
    """Значения и примеры для пачки слов. tr: AnthropicTranslator (его клиент и модель)."""
    raw, usage = await _ask(tr, build(game, words, source_lang, target_lang), "значения слов")
    items = parse(raw, words)
    if not items:
        log.warning("значения слов новеллы не разобрались: %r", raw[:300])
        raise TranslationError("Значения слов не пришли. Попробуй ещё раз.")
    return items, usage


async def levels(tr: object, game: str, items: list[str]) -> tuple[dict[str, str], tuple[int, int] | None]:
    raw, usage = await _ask(tr, build_levels(game, items), "уровни слов")
    got = parse_levels(raw, items)
    if not got:
        log.warning("уровни слов новеллы не разобрались: %r", raw[:300])
        raise TranslationError("Уровни слов не пришли. Попробуй ещё раз.")
    return got, usage


async def in_batches(
    items: list[T],
    size: int,
    run: Callable[[list[T]], Awaitable[R]],
    progress: Callable[[int], None] | None = None,
) -> tuple[list[R], list[BaseException]]:
    """Пачки по size, не больше PARALLEL одновременно. progress(сколько элементов готово).
    Ошибки пачек не роняют остальные: возвращаются отдельно."""
    sem = asyncio.Semaphore(PARALLEL)
    done = 0
    results: list[R] = []
    errors: list[BaseException] = []

    async def one(batch: list[T]) -> None:
        nonlocal done
        async with sem:
            try:
                results.append(await run(batch))
            except Exception as exc:  # одна пачка не должна ронять остальные
                errors.append(exc)
            done += len(batch)
            if progress is not None:
                progress(done)

    await asyncio.gather(*(one(items[i : i + size]) for i in range(0, len(items), size)))
    return results, errors
