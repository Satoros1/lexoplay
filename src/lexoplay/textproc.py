"""Из строк OCR собираем реплику: склейка переносов, имя говорящего, проверка «есть ли текст».

Ещё убираем повтор: бывает, что тот же текст на экране дважды (в Danganronpa правило
школы стоит в рамке на фоне и тут же в окне диалога), и OCR читает обе копии. Тогда
модель переводит реплику дважды или даже досочиняет связку между копиями.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from lexoplay.dedupe import normalize_text, similarity
from lexoplay.geometry import Rect
from lexoplay.ocr_model import Line, OcrResult, Word

REPEAT_SAME = 0.85  # так похожи две копии текста (OCR читает их чуть по-разному)
REPEAT_MIN_CHARS = 15  # короче не трогаем: «No! No!» и «Wait. Wait.» повторяют нарочно
REPEAT_MIN_WORDS = 3
# Повтор одного предложения внутри реплики: только длинное. Короткое повторяют нарочно:
# «Where's your evidence...? Y-Yeah, where's your evidence!?»
REPEAT_SENTENCE_MIN_CHARS = 25
REPEAT_SENTENCE_MIN_WORDS = 6
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


def _long_sentence(norm: str) -> bool:
    return len(norm) >= REPEAT_SENTENCE_MIN_CHARS and len(norm.split()) >= REPEAT_SENTENCE_MIN_WORDS


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
        if _long_sentence(n) and any(_long_sentence(k) and similarity(n, k) >= REPEAT_SAME for k in seen):
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


_CAPS_WORD = re.compile(r"^[A-Z]{3,}$")


def _caps_only(words: list[str]) -> bool:
    return 1 <= len(words) <= 2 and all(_CAPS_WORD.match(w) for w in words)


def _without_labels(result: OcrResult, *, keep_first: bool) -> list[str]:
    """Надписи интерфейса в окне диалога, а не реплика: «SPEAKING» у Danganronpa, «HELP».

    Это одно-два слова капсом без знаков: отдельной строкой (кроме первой, там бывает имя) или
    в конце строки, но далеко справа от текста. Трогаем, только если сама реплика не капсом."""
    raw = [line for line in result.lines if line.text.strip()]
    if not any(re.search(r"[a-z]", line.text) for line in raw):
        return [line.text for line in raw]
    out: list[str] = []
    for i, line in enumerate(raw):
        words = list(line.words)
        texts = [w.text for w in words]
        if _caps_only(texts) and (i > 0 or not keep_first):
            continue
        for n in (2, 1):  # «It's just      SPEAKING»: надпись на той же высоте, но поодаль
            if len(words) > n and _caps_only(texts[-n:]):
                prev, label = words[-n - 1].rect, words[-n].rect
                if label.x - prev.right > 1.5 * max(label.h, prev.h):
                    words, texts = words[:-n], texts[:-n]
                    break
        if texts:
            out.append(" ".join(texts))
    return out or [line.text for line in raw]


def _segments(line: Line) -> list[list[Word]]:
    """Куски строки, разделённые большим промежутком (надпись интерфейса поодаль от текста)."""
    words = list(line.words)
    if not words:
        return []
    out = [[words[0]]]
    for prev, w in zip(words, words[1:], strict=False):
        if w.rect.x - prev.rect.right > 1.5 * max(w.rect.h, prev.rect.h):
            out.append([w])
        else:
            out[-1].append(w)
    return out


def name_from(result: OcrResult) -> str:
    """Текст из рамки для имени: само имя, без надписей рядом.

    У Danganronpa на плашке имени справа написано «SPEAKING»: «Celeste      SPEAKING» это
    «Celeste». Имя капсом («MONOKUMA») остаётся: из двух кусков капсом берём левый."""
    texts = [" ".join(w.text for w in seg) for line in result.lines for seg in _segments(line)]
    texts = [t for t in texts if t.strip()]
    if not texts:
        return ""
    keep = [t for t in texts if not _caps_only(t.split())] or texts
    return keep[0]


def _letters(s: str) -> str:
    return re.sub(r"[^\w]+", "", s).lower()


def _without_name(lines: list[str], speaker: str) -> list[str]:
    """Окно диалога захватило и плашку с именем: имя из рамки для имени в тексте реплики не нужно.

    «Sakura Ogani» (OCR ошибся в букве) в первой строке при имени «Sakura Ogami» тоже имя."""
    from difflib import SequenceMatcher

    name = _letters(speaker)
    if not name or not lines:
        return lines
    first = lines[0]
    head = _letters(first)
    if SequenceMatcher(None, head, name).ratio() >= 0.75:
        return lines[1:]
    if len(head) >= 4 and (name.startswith(head) or head.startswith(name)) and len(lines) > 1:
        return lines[1:]  # «Celeste» при имени из рамки «Celeste SPEAKING» (или наоборот)
    m = _NAME_COLON.match(first)  # «Sakura Ogami: Indeed...»
    if m and SequenceMatcher(None, _letters(m.group(1)), name).ratio() >= 0.75:
        return [m.group(2), *lines[1:]]
    words = first.split()
    for n in (len(speaker.split()), len(speaker.split()) + 1):  # имя и текст OCR склеил в одну строку
        if 0 < n < len(words) and SequenceMatcher(None, _letters(" ".join(words[:n])), name).ratio() >= 0.8:
            return [" ".join(words[n:]), *lines[1:]]
    return lines


_TAIL = re.compile(r"(?<=[.!?…\"'”’»)])\s+([a-z|!][a-z|!1]{0,3})\s*$")
_STROKES = re.compile(r"^[lij|!1]+$")  # стрелка «дальше» в углу окна диалога читается как l, li, lij, |


def drop_tail_noise(text: str) -> str:
    """«Is that what you want to say? lij» -> без «lij».

    После конца последней фразы 1-4 строчных знака, которые не слово: это значок в углу окна
    диалога, а не текст. Настоящее слово («...why», «...huh») остаётся."""
    m = _TAIL.search(text)
    if m is None:
        return text
    token = m.group(1)
    if not _STROKES.match(token):
        from lexoplay.rarity import INTERJECTIONS
        from lexoplay.wordlist_en import WORDS

        if token in WORDS or token in INTERJECTIONS:
            return text
    return text[: m.start()].rstrip()


def to_utterance(result: OcrResult, name_text: str | None = None, *, guess_speaker: bool = True) -> Utterance:
    """OcrResult области диалога -> (говорящий, текст).

    name_text: текст из отдельной рамки для имени, если игрок её задал. Иначе пробуем угадать:
    «Mira: Hello», «[Mira] Hello» или короткая первая строка из имени без знаков в конце.
    """
    if name_text is not None and not has_text(join_lines([name_text])):
        name_text = None  # рамка имени пустая (никто не говорит или не прочиталась): угадываем по тексту
    lines = _without_labels(result, keep_first=name_text is None)
    if name_text is not None:
        speaker = join_lines([name_text])
        return Utterance(speaker, drop_tail_noise(drop_repeats(_without_name(lines, speaker))))
    if not lines:
        return Utterance(None, "")
    if guess_speaker:
        first = lines[0]
        m = _NAME_BRACKETS.match(first) or _NAME_COLON.match(first)
        if m and has_text(m.group(1)):
            rest = [m.group(2)] + lines[1:] if m.group(2).strip() else lines[1:]
            return Utterance(m.group(1).strip(), drop_tail_noise(drop_repeats(rest)))
        if len(lines) >= 2 and _looks_like_name(first, lines[1:]):
            return Utterance(first.strip(), drop_tail_noise(drop_repeats(lines[1:])))
    return Utterance(None, drop_tail_noise(drop_repeats(lines)))


_OPENERS = "\"'“‘«([.…¿¡"


def strip_speaker(text: str, speakers: list[str] | tuple[str, ...] | set[str] = ()) -> str:
    """Пример к слову без имени говорящего впереди: «Kyoko Kirigiri: If the killer...»,
    «Byakuya Togami So, you have no rebuttal?» (имя склеилось с репликой в 0.6.13 и раньше).

    Имя без двоеточия убираем, только если это известный говорящий игры и после него
    начинается новая фраза (с заглавной): «Makoto Naegi is here» остаётся как есть."""
    t = text.strip()
    names = sorted({s.strip() for s in speakers if s and s.strip()}, key=len, reverse=True)
    low = t.casefold()
    for name in names:
        n = name.casefold()
        if not low.startswith(n) or len(t) <= len(name) + 1:
            continue
        rest = t[len(name) :]
        if rest[0] not in " :：":
            continue
        colon = rest.lstrip()[:1] in ":："
        rest = rest.lstrip(" :：")
        if rest and (colon or rest[0].isupper() or rest[0] in _OPENERS):
            return rest
    m = _NAME_COLON.match(t)
    if m:  # «Old Man: Back again?»: подпись из двух-трёх слов с заглавной, даже если имени нет в списке
        words = m.group(1).split()
        if 2 <= len(words) <= 3 and all(w[:1].isupper() for w in words) and m.group(2)[:1].isupper():
            return m.group(2).strip()
    return t


# ------------------------------------------------------------------ текст на весь экран (NVL)
PARA_GAP = 0.55  # пустое место между строками больше стольких высот строки: новый абзац
PARA_INDENT = 1.2  # строка начинается правее абзаца на столько высот строки: красная строка
PARA_SHORT = 0.8  # прошлая строка короче стольких ширин абзаца и кончается точкой: абзац кончился


@dataclass(frozen=True, slots=True)
class Paragraph:
    """Абзац страницы: текст, говорящий (если «Имя: ...»), рамка в долях кадра и его строки."""

    text: str
    speaker: str | None
    rect: Rect
    lines: tuple[Line, ...]

    @property
    def line_h(self) -> float:
        """Высота строки (в долях кадра): от неё размер шрифта перевода поверх абзаца."""
        return _line_h(list(self.lines)) or (self.rect.h / max(1, len(self.lines)))


def _line_h(lines: list[Line]) -> float:
    hs = sorted(ln.rect.h for ln in lines if ln.rect.h > 0)
    return hs[len(hs) // 2] if hs else 0.0


def paragraphs(result: OcrResult) -> list[Paragraph]:
    """Страница визуальной новеллы (NVL) по абзацам.

    Абзацы в таких играх разделены пустой строкой, красной строкой или просто короткой последней
    строкой с точкой. Переводим и показываем перевод по абзацам: новый абзац переводится сам по
    себе, а старые на странице остаются переведёнными."""
    lines = [ln for ln in result.lines if ln.words and has_text(ln.text)]
    if not lines:
        return []
    lines.sort(key=lambda ln: (round(ln.rect.y, 3), ln.rect.x))
    h = _line_h(lines) or 0.02
    page_left = min(ln.rect.x for ln in lines)
    page_w = max(ln.rect.right for ln in lines) - page_left
    groups: list[list[Line]] = [[lines[0]]]
    for ln in lines[1:]:
        cur = groups[-1]
        prev = cur[-1]
        left = min(x.rect.x for x in cur)
        gap = ln.rect.y - prev.rect.bottom
        prev_text = prev.text.rstrip()
        ended = prev_text[-1:] in ".!?…\"”»"
        short = page_w > 0 and prev.rect.right - page_left < PARA_SHORT * page_w
        opens = ln.text.lstrip()[:1] in "\"“«「『(" or _NAME_COLON.match(ln.text) is not None  # реплика: «"...» или «Rin: ...»
        new = gap > PARA_GAP * h or (ended and (ln.rect.x - left > PARA_INDENT * h or short or opens))
        if new:
            groups.append([ln])
        else:
            cur.append(ln)
    out: list[Paragraph] = []
    for g in groups:
        text = drop_tail_noise(join_lines([ln.text for ln in g]))
        if not has_text(text):
            continue
        speaker = None
        m = _NAME_BRACKETS.match(text) or _NAME_COLON.match(text)
        if m and has_text(m.group(1)) and m.group(2).strip():
            speaker, text = m.group(1).strip(), m.group(2).strip()
        x1 = min(ln.rect.x for ln in g)
        y1 = min(ln.rect.y for ln in g)
        x2 = max(ln.rect.right for ln in g)
        y2 = max(ln.rect.bottom for ln in g)
        out.append(Paragraph(text, speaker, Rect(x1, y1, x2 - x1, y2 - y1), tuple(g)))
    return out


def page_text(paras: list[Paragraph]) -> str:
    """Весь текст страницы: по нему следим, не появился ли новый абзац."""
    return "\n".join(p.text for p in paras)
