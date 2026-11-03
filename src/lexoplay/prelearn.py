"""Значения слов колоды к части новеллы: своим ключом Anthropic.

В модель уходят только сами слова и название игры, без текста новеллы. Примеры к словам модель
придумывает сама, обычные и нейтральные: ни героев, ни мест, ни событий игры (игрок её ещё не читал).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from lexoplay.translate import TranslationError, friendly_error, lang_name, no_dash, usage_of

log = logging.getLogger(__name__)

PROMPT = """A learner whose native language is {tgt} is about to read the visual novel "{game}" in {src}. Before it they learn words that come up in it often:
{words}
For each word, in the same order, reply with one JSON object per line and nothing else:
{{"word": "<the word as given>", "lemma": "<its dictionary form>", "pos": "<part of speech in English: noun, verb, adj, adv...>", "ipa": "<IPA of the lemma, without slashes>", "meaning": "<1 to 3 short meanings in {tgt}, the most likely ones in a story like this, separated by commas>", "example": "<a short natural {src} sentence of your own, 6 to 14 words, with the word in that meaning>", "example_tr": "<that sentence in {tgt}>"}}
The examples must be everyday sentences that have nothing to do with the game: no characters, places, events or plot of "{game}", the learner has not read it yet. No em dash characters."""
MAX_TOKENS = 3000
TIMEOUT_S = 90.0


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


async def prelearn(
    tr: object, game: str, words: list[str], *, source_lang: str = "en", target_lang: str = "ru"
) -> tuple[list[Prelearned], tuple[int, int] | None]:
    """tr: AnthropicTranslator (его клиент и модель). Без раздумий: ответ простой, а слов бывает 15."""
    a = tr._anthropic  # type: ignore[attr-defined]
    client = tr._client  # type: ignore[attr-defined]
    model = tr._model  # type: ignore[attr-defined]
    params: dict = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "messages": [{"role": "user", "content": build(game, words, source_lang, target_lang)}],
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
        raise TranslationError("Модель не приняла запрос значений слов.")
    raw = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text")
    items = parse(raw, words)
    if not items:
        log.warning("значения слов новеллы не разобрались: %r", raw[:300])
        raise TranslationError("Значения слов не пришли. Попробуй ещё раз.")
    return items, usage_of(msg)
