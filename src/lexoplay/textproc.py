"""Из строк OCR собираем реплику: склейка переносов, имя говорящего, проверка «есть ли текст»."""

from __future__ import annotations

import re
from dataclasses import dataclass

from lexoplay.ocr_model import OcrResult

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


def _looks_like_name(line: str) -> bool:
    words = line.split()
    if not 1 <= len(words) <= 3:
        return False
    if line.rstrip().endswith(_TERMINAL):
        return False
    if not line[:1].isupper():
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
        return Utterance(speaker if speaker and has_text(speaker) else None, join_lines(lines))
    if not lines:
        return Utterance(None, "")
    if guess_speaker:
        first = lines[0]
        m = _NAME_BRACKETS.match(first) or _NAME_COLON.match(first)
        if m and has_text(m.group(1)):
            rest = [m.group(2)] + lines[1:] if m.group(2).strip() else lines[1:]
            return Utterance(m.group(1).strip(), join_lines(rest))
        if len(lines) >= 2 and _looks_like_name(first):
            return Utterance(first.strip(), join_lines(lines[1:]))
    return Utterance(None, join_lines(lines))
