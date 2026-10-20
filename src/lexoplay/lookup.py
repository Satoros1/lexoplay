"""Значение слова для F3 своим ключом Anthropic, без привязки к Lexo.

С привязкой это делает сервер Lexo (/desktop/lookup). Без неё спрашиваем Haiku тем же
промптом, что и сервер: словарная форма, транскрипция, значения именно в этой реплике.
Ответ короткий (JSON на пару сотен токенов), поэтому без потока.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from lexoplay.lexo import Sense
from lexoplay.translate import TranslationError, friendly_error, lang_name, no_dash, usage_of

log = logging.getLogger(__name__)

SENSE_PROMPT = """A learner whose native language is {tgt} clicked on a word in a game (the game is in {src}).
Sentence: "{sentence}"
Clicked: "{selection}"
{translation_line}Reply with JSON only:
{{"lemma": "<dictionary form of the clicked word or phrase; for a phrasal verb or idiom the whole expression, e.g. \\"make amends\\">",
 "pos": "<part of speech in English: noun, verb, adj, adv, phrase...>",
 "ipa": "<IPA transcription of the lemma, without slashes>",
 "senses": ["<meaning in {tgt} exactly as used in this sentence>", "<another common meaning, optional>"],
 "marked_translation": "<the sentence translation above with the clicked word's translation wrapped in **double asterisks**, or empty if no translation was given>"}}
Short meanings, a word or a short phrase each, at most 3. No em dash characters."""


def build_prompt(selection: str, sentence: str, sentence_translation: str | None, target_lang: str, source_lang: str) -> str:
    line = f'Sentence translation: "{sentence_translation}"\n' if sentence_translation else ""
    return SENSE_PROMPT.format(
        tgt=lang_name(target_lang),
        src=lang_name(source_lang),
        sentence=sentence.replace('"', "'")[:1500],
        selection=selection.replace('"', "'")[:80],
        translation_line=line,
    )


def parse_sense(raw: str, selection: str) -> Sense:
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        raise ValueError(f"no JSON in reply: {raw[:80]!r}")
    data = json.loads(match.group(0))
    senses = [no_dash(str(x)) for x in data.get("senses", []) if str(x).strip()][:3]
    if not senses:
        raise ValueError("no senses")
    return Sense(
        lemma=str(data.get("lemma") or "").strip().strip('"') or selection.strip(),
        pos=str(data.get("pos") or "").strip(),
        ipa=str(data.get("ipa") or "").strip().strip("/[]"),
        senses=senses,
        marked_translation=no_dash(str(data.get("marked_translation") or "")),
    )


class AnthropicLookup:
    def __init__(self, api_key: str, model: str, *, timeout: float = 15.0) -> None:
        import anthropic

        self._anthropic = anthropic
        self._client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=1)
        self._model = model
        self._thinking_off = True
        self.last_usage: tuple[int, int] | None = None

    async def lookup(
        self,
        *,
        selection: str,
        sentence: str,
        sentence_translation: str | None,
        target_lang: str = "ru",
        source_lang: str = "en",
    ) -> Sense:
        a = self._anthropic
        params: dict = {
            "model": self._model,
            "max_tokens": 600,
            "messages": [
                {"role": "user", "content": build_prompt(selection, sentence, sentence_translation, target_lang, source_lang)}
            ],
        }
        for _attempt in range(3):
            if self._thinking_off:
                params["thinking"] = {"type": "disabled"}
                params.pop("output_config", None)
            else:
                params.pop("thinking", None)
                params["output_config"] = {"effort": "low"}
            try:
                msg = await self._client.messages.create(**params)
            except a.BadRequestError as exc:
                if self._thinking_off and "thinking" in str(exc).lower():
                    self._thinking_off = False
                    continue
                raise friendly_error(a, exc, self._model) from exc
            except Exception as exc:
                raise friendly_error(a, exc, self._model) from exc
            self.last_usage = usage_of(msg)
            raw = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text")
            try:
                return parse_sense(raw, selection)
            except (ValueError, json.JSONDecodeError):
                log.warning("значение слова не разобралось: %r", raw[:200])
        raise TranslationError("Не получилось найти значение, попробуй ещё раз.")

    async def explain(
        self,
        *,
        sentence: str,
        speaker: str | None,
        translation: str | None,
        game: str,
        context: list[tuple[str | None, str]],
        target_lang: str = "ru",
        source_lang: str = "en",
    ) -> Explanation:
        a = self._anthropic
        prompt = build_explain_prompt(
            sentence=sentence,
            speaker=speaker,
            translation=translation,
            game=game,
            context=context,
            target_lang=target_lang,
            source_lang=source_lang,
        )
        params: dict = {"model": self._model, "max_tokens": 1200, "messages": [{"role": "user", "content": prompt}]}
        for _attempt in range(3):
            if self._thinking_off:
                params["thinking"] = {"type": "disabled"}
                params.pop("output_config", None)
            else:
                params.pop("thinking", None)
                params["output_config"] = {"effort": "low"}
            try:
                msg = await self._client.messages.create(**params)
            except a.BadRequestError as exc:
                if self._thinking_off and "thinking" in str(exc).lower():
                    self._thinking_off = False
                    continue
                raise friendly_error(a, exc, self._model) from exc
            except Exception as exc:
                raise friendly_error(a, exc, self._model) from exc
            self.last_usage = usage_of(msg)
            raw = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text")
            try:
                return parse_explanation(raw)
            except (ValueError, json.JSONDecodeError):
                log.warning("разбор реплики не разобрался: %r", raw[:200])
        raise TranslationError("Не получилось разобрать реплику, попробуй ещё раз.")


# ---------------------------------------------------------------------- разбор реплики
EXPLAIN_PROMPT = """A learner whose native language is {tgt} is playing the video game "{game}" in {src}.
Explain one line of its dialogue so they understand what is really said and learn from it.
{context}Line{speaker}: "{sentence}"
{translation_line}Reply with JSON only:
{{"items": [{{"phrase": "<exact words copied from the line>", "meaning": "<what it means here, in {tgt}, a few words>", "note": "<one short sentence in {tgt}: why it is tricky (idiom, slang, phrasal verb, grammar, wordplay, a reference to the game) or how it is used>"}}],
 "summary": "<one or two sentences in {tgt}: the tone of the line and what a word-by-word reading would miss>"}}
Rules: at most 5 items, the most useful for a learner: idioms, slang, phrasal verbs, set phrases, tricky grammar, names and references. Skip simple everyday words. Copy each phrase exactly as it is in the line. Write meanings, notes and the summary in {tgt}. Never use the em dash character."""


@dataclass(frozen=True, slots=True)
class ExplainItem:
    phrase: str
    meaning: str
    note: str = ""


@dataclass(frozen=True, slots=True)
class Explanation:
    items: tuple[ExplainItem, ...]
    summary: str = ""


def build_explain_prompt(
    *,
    sentence: str,
    speaker: str | None,
    translation: str | None,
    game: str,
    context: list[tuple[str | None, str]],
    target_lang: str,
    source_lang: str,
) -> str:
    ctx = ""
    if context:
        lines = "\n".join(f"{(s + ': ') if s else ''}{t}" for s, t in context[-6:])
        ctx = f"Earlier lines, for context:\n{lines}\n"
    return EXPLAIN_PROMPT.format(
        tgt=lang_name(target_lang),
        src=lang_name(source_lang),
        game=(game or "unknown").replace('"', "'"),
        context=ctx,
        speaker=f" ({speaker})" if speaker else "",
        sentence=sentence.replace('"', "'")[:1500],
        translation_line=f'Its translation shown to the player: "{translation}"\n' if translation else "",
    )


def parse_explanation(raw: str) -> Explanation:
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        raise ValueError(f"no JSON in reply: {raw[:80]!r}")
    data = json.loads(match.group(0))
    items = []
    for it in data.get("items") or []:
        if not isinstance(it, dict):
            continue
        phrase = str(it.get("phrase") or "").strip().strip('"«»')
        meaning = no_dash(str(it.get("meaning") or ""))
        if phrase and meaning:
            items.append(ExplainItem(phrase[:80], meaning[:200], no_dash(str(it.get("note") or ""))[:300]))
    summary = no_dash(str(data.get("summary") or ""))[:600]
    if not items and not summary:
        raise ValueError("empty explanation")
    return Explanation(tuple(items[:5]), summary)
