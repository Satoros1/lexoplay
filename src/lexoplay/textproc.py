"""Из строк OCR собираем реплику: склейка переносов, имя говорящего, проверка «есть ли текст».

Ещё убираем повтор: бывает, что тот же текст на экране дважды (в Danganronpa правило
школы стоит в рамке на фоне и тут же в окне диалога), и OCR читает обе копии. Тогда
модель переводит реплику дважды или даже досочиняет связку между копиями.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from lexoplay.dedupe import normalize_text, similarity
from lexoplay.ocr_model import OcrResult

REPEAT_SAME = 0.85  # так похожи две копии текста (OCR читает их чуть по-разному)
REPEAT_MIN_CHARS = 15  # короче не трогаем: «No! No!» и «Wait. Wait.» повторяют нарочно
REPEAT_MIN_WORDS = 3
_SENTENCE = re.compile(r"\S.*?(?:[.!?…]+[\"'»”’)\]]*(?=\s|$)|$)")

_TERMINAL = tuple(".,!?…:;\"'”’»)")
_NAME_COLON = re.compile(r"^\s*([A-Z][\w'.\- ]{0,24}?)\s*[:：]\s+(\S.*)$")
_NAME_BRACKETS = re.compile(r"^\s*[\[【「(]\s*([^\]】」)]{1,24}?)\s*[\]】」)]\s*(.*)$")


@dataclass(frozen=True, slots=True)
class Utterance:
    speaker: str | None
    text: str


def letters_count(text: str) -> int:
    return sum(1 for ch in text if ch.isalpha())


def has_text(text: str) -> bool:
    """Меньше двух букв считаем шумом OCR, а не репликой."""
    return letters_count(text) >= 2


def _long(norm: str) -> bool:
    return len(norm) >= REPEAT_MIN_CHARS and len(norm.split()) >= REPEAT_MIN_WORDS


def _clean_score(text: str) -> float:
    """Насколько чисто прочитан текст: сколько в нём настоящих слов (копия на фоне часто бледнее)."""
    from lexoplay.ocrscore import token_score

    total = 0.0
    for tok in text.split():
        weight, score = token_score(tok)
        total += weight * (score - 0.5)
    return total


def drop_repeats(lines: list[str]) -> str:
    """Строки окна диалога в реплику без повторённого текста (две копии на экране).

    Сначала целыми блоками: верхние строки повторяют нижние (рамка на фоне и окно диалога
    переносят строки по-разному, поэтому сравниваем склеенный текст). Потом по предложениям:
    длинное предложение, которое уже было, второй раз не берём."""
    lines = [x for x in lines if x.strip()]
    for cut in range(1, len(lines)):
        top, bottom = join_lines(lines[:cut]), join_lines(lines[cut:])
        nt, nb = normalize_text(top), normalize_text(bottom)
        if _long(nt) and _long(nb) and similarity(nt, nb) >= REPEAT_SAME:
            # берём копию, прочитанную чище; поровну: нижнюю (окно диалога обычно внизу, рамка с текстом над ним)
            return top if _clean_score(top) > _clean_score(bottom) else bottom
    text = join_lines(lines)
    sentences = [s.strip() for s in _SENTENCE.findall(text) if s.strip()]
    if len(sentences) < 2:
        return text
    kept: list[str] = []
    seen: list[str] = []
    for s in sentences:
        n = normalize_text(s)
        if _long(n) and any(similarity(n, k) >= REPEAT_SAME for k in seen):
            continue
        kept.append(s)
        seen.append(n)
    return text if len(kept) == len(sentences) else " ".join(kept)


def join_lines(lines: list[str]) -> str:
    """Строки окна диалога в одну реплику.

    Перенос с дефисом в конце строки склеиваем в одно слово ("remem-" + "ber"),
    остальные строки соединяем пробелом.
    """
    out = ""
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if not out:
            out = line
        elif out.endswith("-") and not out.endswith("--") and line[:1].islower():
            out = out[:-1] + line
        else:
            out = f"{out} {line}"
    return re.sub(r"\s+", " ", out).strip()


# слова, с которых начинается фраза, а не имя: «HEY YOU», «I KNOW», «WHAT THE»
_NOT_NAME = {
    "i", "you", "we", "they", "he", "she", "it", "me", "him", "her", "us", "them", "my", "your", "our", "his",
    "its", "their", "this", "that", "these", "those", "the", "a", "an", "is", "are", "was", "were", "be", "am",
    "do", "does", "did", "don't", "can", "can't", "will", "won't", "would", "should", "could", "have", "has",
    "had", "not", "no", "yes", "yeah", "ok", "okay", "hey", "hi", "hello", "oh", "ah", "uh", "um", "well", "so",
    "and", "but", "or", "if", "then", "what", "who", "why", "how", "where", "when", "which", "there", "here",
    "please", "thanks", "sorry", "wait", "look", "listen", "come", "go", "get", "let", "let's", "just", "now",
    "i'm", "you're", "it's", "that's", "what's", "i'll", "we're", "they're", "all", "every", "some", "any",
    "to", "of", "in", "on", "at", "for", "with", "from", "up", "out", "too", "very", "really",
}


def _looks_like_name(line: str, rest: list[str] | None = None) -> bool:
    words = line.split()
    if not 1 <= len(words) <= 3:
        return False
    if line.rstrip().endswith(_TERMINAL):
        return False
    if not line[:1].isupper():
        return False
    if any(w.strip(".,!?'\"").lower() in _NOT_NAME for w in words):
        return False  # «HEY YOU», «I KNOW»: начало фразы, перенесённой на вторую строку
    if rest and line.isupper() and len(words) > 1:
        # весь текст капсом (пиксельный шрифт): короткая первая строка из двух-трёх слов
        # чаще кусок фразы, чем имя. Одно слово капсом («MIRA») всё ещё похоже на имя.
        caps = sum(1 for r in rest if r.strip() and r.isupper())
        if caps >= max(1, len(rest) // 2):
            return False
    # имя: каждое слово с заглавной ("Old Man"), без цифр
    return all(w[:1].isupper() for w in words) and not any(ch.isdigit() for ch in line)


def to_utterance(result: OcrResult, name_text: str | None = None, *, guess_speaker: bool = True) -> Utterance:
    """OcrResult области диалога -> (говорящий, текст).

    name_text: текст из отдельной рамки для имени, если игрок её задал. Иначе пробуем угадать:
    «Mira: Hello», «[Mira] Hello» или короткая первая строка из имени без знаков в конце.
    """
    lines = [line.text for line in result.lines if line.text.strip()]
    if name_text is not None:
        speaker = join_lines([name_text]) or None
        return Utterance(speaker if speaker and has_text(speaker) else None, drop_repeats(lines))
    if not lines:
        return Utterance(None, "")
    if guess_speaker:
        first = lines[0]
        m = _NAME_BRACKETS.match(first) or _NAME_COLON.match(first)
        if m and has_text(m.group(1)):
            rest = [m.group(2)] + lines[1:] if m.group(2).strip() else lines[1:]
            return Utterance(m.group(1).strip(), drop_repeats(rest))
        if len(lines) >= 2 and _looks_like_name(first, lines[1:]):
            return Utterance(first.strip(), drop_repeats(lines[1:]))
    return Utterance(None, drop_repeats(lines))
