"""Перевод реплики.

Основной путь: сервер Lexo (/desktop/translate), там кеш, лимит и языки из профиля.
Запасной путь без привязки: Claude Haiku напрямую своим ключом Anthropic.

Напрямую:
* модель немного думает перед ответом (adaptive thinking, effort medium): перевод ровнее
  на секунду-другую дольше. В настройках это выключается, тогда thinking выключен совсем;
* модель отвечает переводом, а после строки «@@» той же репликой по-английски без ошибок
  OCR: её показывает режим слов. Без JSON, поэтому перевод виден сразу, пока пишется;
* ответ идёт потоком: первые слова видны в строке, пока модель дописывает остальное;
* соединение с сервером открывается заранее, пока идёт распознавание.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
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
LATIN_TARGETS = frozenset({"en", "nl", "de", "es", "fr", "it", "pt", "pl"})  # перевод латиницей

# Версия промпта и обработки перевода. Меняется, когда перевод заметно меняется: кеш
# реплик со старой версией не используется, они переведутся заново уже по-новому.
# 4: модель думает перед переводом и чинит оригинал после «@@»
# 5: рассуждения модели, попавшие в ответ, и подпись имени вырезаются (в 4 они бывали в переводе)
# 6: имя говорящего больше не попадает в текст реплики (в 5 из-за «SPEAKING» на плашке имени попадало,
#    а похожая реплика без имени потом находилась в кеше вместе со старым переводом с именем)
# 7: справка по игре (герои, термины, тон) и правила понимать реплику через игру; нарочные повторы
#    внутри реплики больше не выбрасываются
TRANSLATION_VERSION = 7

SYSTEM_PROMPT = """You are the localizer of the video game "{game}". The player reads its dialogue in {target}: you translate it line by line while they play.

Write the translation a professional {target} game localizer would ship:
- Natural, idiomatic {target} that a native speaker would really say in this situation. Rephrase freely; never translate word for word, never use calques, rare, bookish or made-up words.
- Translate the whole meaning: keep every clause, qualifier and detail of the line (a shorter translation that loses a part of it is wrong), never drop a word and never put "..." in place of a word you are unsure about.
- Translate only what the line says: never add a meaning, a reason or an ending of your own. If the OCR lost words or the line breaks off mid-sentence, translate what is there and end with "...".
- Keep the perspective: a line in which the speaker talks about themselves stays in the first person and is never retold ("he says that...", "she asks..."). Never talk to the player, never add the speaker's name.
- A line with a speaker is that character's own words. A line without a speaker is narration, the hero's thoughts, a sign or a menu: translate it as such.
- Use what you know about {game}: its world, the story so far, the characters, how they relate to each other and how they talk, its mechanics and terms (the game notes below sum this up). Read every line in that light: a line that looks odd or garbled usually means something specific in this game (a mechanic, an item, a nickname, a running joke, a callback to an earlier scene), so translate that meaning, not the words one by one. Your knowledge only explains the line: never add facts the line doesn't state and never reveal what happens later in the game.
- Game terms (mechanics, modes, items, titles, places) read like the proper name of that thing in {target} and stay the same every time: take them from the glossary or the game notes. A term that isn't there gets a short natural {target} name a player would understand, never a transliteration of the English sounds.
- Tutorial and system lines explain the controls and mechanics: make them clear and exact, with the same terms the game uses elsewhere.
- The result must make sense in the scene. If you can't make sense of a line even with the context and the game in mind, translate what is clear and keep it plain; never write nonsense.
- Keep each character's voice: slang, rudeness, jokes, hesitation, politeness. Stay consistent with the earlier translations: the same form of address between the same characters (ты/вы and the like), the same genders, the same names and terms. The glossary wins over everything. Write names in the {target} alphabet by its usual transliteration rules; when {target} has its own alphabet, never leave a name in Latin letters.
- The text comes from OCR and can be garbled: l/I, 0/O, rn/m, split or merged words ("vi ew", "becamewarped"), stray symbols like | or #, a few stray letters after the last sentence ("... say? lij": that is the text box arrow, drop it). Work out the words the game really shows from the letters and the context, then translate them. ALL CAPS is just the game's font: use normal capitalization.
- The screen sometimes shows the same text twice (say, in a box behind the dialogue), so a long sentence may come twice: translate it once and never invent a link between the copies. A character repeating their own words on purpose is not that ("Where's your evidence...? Y-Yeah, where's your evidence!?"): translate every part.
- If the speaker label looks like a piece of the sentence rather than a name, treat it as part of the line.
- Stuttering and drawn-out words stay that way in the translation (b-b-blabbermouth → б-б-болтун, Nooo → Нееет). A word in *asterisks* is emphasis: keep the emphasis in the wording, drop the asterisks.
- Russian and similar languages mark the speaker's gender in verbs and adjectives: work it out from the speaker's name and the earlier lines, and keep it the same for each character.
- Punctuation: keep ? ! and three dots (...) as in the original, and a period where it has one. A line that starts with "..." continues the previous text box: start the translation with "..." too. If the original ends with a comma or breaks off because the sentence continues in the next text box, end with "..." and never with a comma.
{examples}
Reply with the translation of the line after "Translate this line". Then on a new line write "@@" and that line in {source} as the game really shows it: the OCR mistakes fixed, the same words and punctuation, normal capitalization, no speaker name. Nothing else: no quotes, no notes.
Work out the garbled words silently, before you answer: the reply is shown to the player as is, so it never contains your reasoning, guesses about the OCR, corrections of yourself, alternatives or a second version: write the translation once, from start to end. If you can't be sure of a word, choose the most likely one and translate.
When the request ends with <study>, the player is learning {source} at the level given there and reads the original first, so they need two more things from you.
First, before the translation, write one line that starts with "##": the parts of this line such a learner most likely won't understand, the most important first, no more than the max given, as "## part = meaning; part = meaning". {study_rules} A line with nothing hard gets just "##".
Second, translate closely: keep every part of the line and, where {target} allows, its order, so the player can match each phrase of the original with its translation. It still reads as natural {target}, but nothing is retold, merged or left out. Then the "@@" line as usual.
Never use the em dash character; use a comma, a colon or a new sentence instead.{notes}"""

SOURCE_MARK = "@@"
HINT_MARK = "##"  # учёба (study.py): строка подсказок к трудным местам реплики, перед переводом
HINT_CHARS = 32
HINTS_MAX_TOKENS = 300

# Что подсказывать в учёбе: одно и то же для перевода и для отдельного запроса подсказок.
STUDY_RULES = (
    "A part is copied from the line exactly as it stands. Prefer whole expressions whose meaning doesn't follow "
    "from their words (idioms, phrasal verbs, set phrases and patterns such as \"figure out\", \"no way to\", "
    "\"as it turns out\", \"make up one's mind\") over single words, and take a single word only if it is rare; never explain one word of "
    "an expression on its own. The meaning is a short {target} rendering that fits this line, 1-5 words. Skip names, "
    "the game's own terms, what any learner of that level knows and every part listed as known."
)

# Подсказки к реплике, перевод которой уже есть (из истории или с сервера Lexo): маленький запрос без раздумий.
HINTS_PROMPT = """The player reads the video game "{game}" in {source} and is learning the language at the level given in <study>. They read the line in the original first.
Pick the parts of the line such a learner most likely won't understand, the most important first, no more than the max given, and reply with one line only: "## part = meaning; part = meaning". {study_rules} If nothing in the line is hard, reply just "##". Never use the em dash character."""

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
        ("You're not a b-b-blabbermouth, right? This h-has to be a secret!", "Ты ведь не б-б-болтун, правда? Это д-должно остаться в секрете!"),
        ("Feels gross? Hell no. Feels *great*, more like!", "Противно? Чёрта с два. Скорее, кайфово!"),
    ],
}


def examples_block(target_lang: str) -> str:
    pairs = EXAMPLES.get(target_lang)
    if not pairs:
        return ""
    lines = "\n".join(f"{en} → {tr}" for en, tr in pairs)
    return f"\nExamples of the style we want (original → translation):\n{lines}\n"


# Справка по игре: один раз на игру модель посильнее пишет, что это за игра, как зовут героев
# по-русски, какого они пола и как говорят, как называются термины. Справка идёт в промпт каждой
# реплики (кешируется вместе с ним), поэтому имена и термины одинаковые, а странные реплики
# понимаются через игру.
NOTES_MAX_CHARS = 7000
NOTES_MAX_TOKENS = 6000
NOTES_TIMEOUT_S = 150.0
NOTES_SAMPLE_LINES = 40

NOTES_PROMPT = """You prepare notes for a localizer who translates the video game "{game}" from {source} into {target} line by line while the player plays.
Use only what you really know about this game. If you don't know it at all, reply with the single word UNKNOWN.
No spoilers: describe the game, the characters and the terms as they are at the start of the game, plus what the lines from the player's screen already show. Never reveal twists, culprits, deaths, secret identities or anything else the player hasn't reached.

The player reads these notes too: write the descriptions in {target}; the names and terms on the left stay as the game writes them.
Reply in exactly this format, plain text, no markdown:
GAME: 2-3 sentences: genre, setting, tone, who the player character is, how the story is told.
CHARACTERS:
English name = {target} spelling | gender as presented at the start | their title or role | how they talk and address others, in a few words
(the main and recurring characters, up to 25 lines)
TERMS:
English term = {target} translation | a few words of what it is
(game-specific mechanics, modes, items, titles, places, organizations, catchphrases: up to 40 lines. Use the established official or fan {target} translation if there is one; otherwise a short natural {target} name a player would understand, not a transliteration of the English sounds)
STYLE: 1-3 sentences: the tone the translation should keep, and ты/вы or similar forms between the characters where it matters."""


def build_notes_user(game: str, lines: list[tuple[str | None, str]], speakers: list[str]) -> str:
    parts = [f'Game: "{game}"']
    if speakers:
        parts.append("Characters the player has met (as the game writes them): " + ", ".join(speakers[:40]))
    if lines:
        parts.append("Lines from the player's screen, oldest first (OCR, may be garbled):")
        parts += [_line(sp, text) for sp, text in lines[-NOTES_SAMPLE_LINES:]]
    parts.append("Write the notes.")
    return "\n".join(parts)


def clean_notes(text: str) -> str | None:
    """Ответ модели -> справка. None: модель не знает игру."""
    t = text.strip().strip("`").strip()
    if not t or t.upper().startswith("UNKNOWN") or len(t) < 40:
        return None
    t = re.sub(r"\*\*|^#+\s*", "", t, flags=re.MULTILINE)  # markdown, если всё же написала
    return t[:NOTES_MAX_CHARS].strip()


MAX_TOKENS = 1024
SHORT_WORDS = 4  # реплика не длиннее стольких слов: короткая («Huh?», «W-wait!», «I see.»)
SHORT_CHARS = 40
SHORT_CONTEXT = 3  # короткой реплике хватает трёх прошлых (кто говорит и с кем, ты или вы)


def is_short(text: str) -> bool:
    """«Huh?», «Yeah...», «W-wait!», «Makoto, wait!»: перевод без раздумий и без всей сцены."""
    t = text.strip()
    return bool(t) and len(t) <= SHORT_CHARS and len(re.findall(r"[^\W\d_]+(?:['’-][^\W\d_]+)*", t)) <= SHORT_WORDS
MAX_TOKENS_THINKING = 4096  # thinking тратит те же max_tokens, что и ответ


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
    notes: str = ""  # справка по игре: герои, термины, тон (game_notes)
    # учёба: игрок читает оригинал сам (уровень B1): модель подсказывает трудные места и переводит близко к тексту.
    # study_known: места, которые он уже знает или учит; study_room: сколько новых подсказок можно
    study: bool = False
    study_level: str = "B1"
    study_known: tuple[str, ...] = ()
    study_room: int = 3
    # кусок перевода по мере того, как модель его пишет (из фонового потока)
    on_partial: Partial | None = field(default=None, compare=False, repr=False)
    # учёба: подсказки, как только модель их написала, раньше перевода (из фонового потока)
    on_hints: Callable[[list[tuple[str, str]]], None] | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class TranslateResult:
    source: str
    translation: str
    usage: tuple[int, int] | None = None  # токены своего ключа (вход, выход): для счётчика расходов
    # учёба: (место реплики, значение) по важности; None: модель строку подсказок не написала
    hints: tuple[tuple[str, str], ...] | None = None


class Translator(Protocol):
    async def translate(self, req: TranslateRequest) -> TranslateResult: ...


def lang_name(code: str) -> str:
    return LANGS.get(code, code)


def notes_block(notes: str) -> str:
    notes = notes.strip()
    if not notes:
        return ""
    return (
        "\n\nGame notes: what is known about this game, its characters and terms. Follow them for names, genders, "
        "terms and tone (the glossary wins over them); they only help you understand the lines and are never "
        "quoted or retold in the translation.\n<game_notes>\n" + notes[:NOTES_MAX_CHARS] + "\n</game_notes>"
    )


def build_system(req: TranslateRequest) -> str:
    return SYSTEM_PROMPT.format(
        target=lang_name(req.target_lang),
        source=lang_name(req.source_lang),
        game=(req.game or "unknown").replace('"', "'"),
        examples=examples_block(req.target_lang),
        notes=notes_block(req.notes) + glossary_block(req.glossary),
        study_rules=STUDY_RULES.format(target=lang_name(req.target_lang)),
    )


def glossary_block(glossary: dict[str, str]) -> str:
    """Глоссарий игры постоянный: в системной части он кешируется вместе с промптом."""
    if not glossary:
        return ""
    rows = "\n".join(f"{k} => {v}" for k, v in glossary.items())
    return "\n\nGlossary of this game (always use these names and terms):\n<glossary>\n" + rows + "\n</glossary>"


def _attr(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "'").replace("<", "‹").replace(">", "›")


def _line(speaker: str | None, text: str) -> str:
    who = f' speaker="{_attr(speaker)}"' if speaker else ""
    return f"<line{who}>{text}</line>"


def build_user(req: TranslateRequest) -> str:
    """Прошлые реплики с их переводами (от старых к новым) и сама реплика (глоссарий в системной части).

    Прошлые реплики не только для понимания сцены: по ним модель держит те же имена,
    пол персонажей и «ты/вы», что уже звучали в переводе."""
    parts = []
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
    if req.study:
        parts.append(study_tag(req.study_level, req.study_room, req.study_known))
    return "\n".join(parts)


def study_tag(level: str, room: int, known: tuple[str, ...] | list[str]) -> str:
    body = ("known: " + "; ".join(_attr(k) for k in known)) if known else ""
    return f'<study level="{_attr(level)}" max="{max(1, room)}">{body}</study>'


_GLOSS_LINE = re.compile(r"(?m)^[ \t]*##[^\n]*(?:\n|$)")
_HINT_ITEM = re.compile(r"^[\"'«“]?([A-Za-z0-9][A-Za-z0-9'’ ,.!?-]{0,80}?)[\"'»”]?\s*(?:=|—|–|\s-\s)\s*(.+?)$")


def split_hints(raw: str) -> tuple[str, str | None, bool]:
    """Ответ модели -> (ответ без строки подсказок, строка подсказок «## ...» или None, дописана ли она)."""
    m = _GLOSS_LINE.search(raw)
    if m is None:
        if raw.strip() == "#":
            return "", None, False  # «#» уже пришёл, второй ещё нет: строка значений дописывается
        return raw, None, False
    line = m.group(0)
    return raw[: m.start()] + raw[m.end() :], line.strip(), line.endswith("\n")


def short_hint(text: str) -> str:
    t = no_dash(text.strip().strip("\"'«»“”").strip()).rstrip(".").strip()
    if len(t) > HINT_CHARS:
        cut = t[: HINT_CHARS + 1]
        t = (cut.rsplit(" ", 1)[0] if " " in cut else cut[:HINT_CHARS]).rstrip(",;: ") + "…"
    return t


def parse_hints(line: str | None) -> list[tuple[str, str]]:
    """«## the way things are = при нынешнем положении дел; persuade = убедить» -> [(место, значение), ...] по порядку.

    Пустая строка «##» (подсказывать нечего) -> []. Места без значения (« = -») не берём."""
    if not line:
        return []
    body = line.strip().lstrip("#").strip()
    out: list[tuple[str, str]] = []
    for part in re.split(r"\s*[;|]\s*", body):
        m = _HINT_ITEM.match(part.strip())
        if not m:
            continue
        item = m.group(1).strip().strip(",.!? ").replace("’", "'")
        meaning = m.group(2).strip()
        if not item or meaning.strip("\"'«»“” ") in ("-", "—", "–", ""):
            continue
        out.append((item, short_hint(meaning)))
    return out


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


_LEAD_DOTS = re.compile(r"^\s*[\"'“«(]?\s*(?:\.{2,}|…)")


def fix_start(source: str, translation: str) -> str:
    """Оригинал начинается с «...» (продолжение прошлого окна диалога): перевод тоже."""
    if translation and _LEAD_DOTS.match(source) and not _LEAD_DOTS.match(translation):
        return "..." + translation.lstrip()
    return translation


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
_QUOTE_PAIRS = {'"': '"', "'": "'", "«": "»", "“": "”", "„": "“", "`": "`"}


def unwrap_quotes(text: str, source: str | None = None) -> str:
    """Модель обернула весь ответ в кавычки: снять. Но «Ты опоздал», сказала она. остаётся как есть:
    кавычка там часть фразы, а не обёртка."""
    t = text.strip()
    src = (source or "").strip()
    quoted_source = bool(src) and src[0] in _QUOTES and src[-1] in _QUOTES
    while len(t) >= 2 and not quoted_source:
        close = _QUOTE_PAIRS.get(t[0])
        inner = t[1:-1]
        if close is None or t[-1] != close or close in inner or t[0] in inner:
            break
        t = inner.strip()
    return t
_LABEL = re.compile(r"^(translation|перевод)\s*:\s*", re.I)


_TAG = re.compile(r"</?(?:translation|line)[^>]*>", re.I)

# модель «думает вслух» прямо в ответе: такие строки в перевод не идут
_THINKING = re.compile(
    r"^\W*(?:wait\b|hmm+\b|actually\b|let me\b|i need\b|i'll\b|i will\b|i think\b|i'm not sure\b|"
    r"note\b|the ocr\b|probably\b|likely\b|maybe\b|best (?:reconstruction|guess)|reconstruct|alternatively\b|"
    r"here(?:'s| is)\b)",
    re.I,
)
_LATIN = re.compile(r"[A-Za-z]")
_LABEL_NAME = re.compile(r"^([^\W\d_][^\s:,.!?]{0,19}(?: [^\W\d_][^\s:,.!?]{0,19}){0,2}):\s+(?=\S)")
_SOURCE_LABEL = re.compile(r"^([A-Z][\w'.-]{0,19}(?: [A-Z][\w'.-]{0,19}){0,2}):\s+(?=\S)")


def _thinking_line(line: str, target_lang: str) -> bool:
    if "OCR" in line or _THINKING.match(line):
        return True
    if target_lang in LATIN_TARGETS:
        return False
    # перевод не латиницей (русский, японский...), а строка почти вся латиницей: это не перевод
    latin = len(_LATIN.findall(line))
    native = sum(1 for ch in line if ch.isalpha() and not ch.isascii())
    return latin >= 12 and latin > native * 1.5


def drop_thinking(text: str, target_lang: str = "ru", *, partial: bool = False) -> str:
    """Строки, где модель рассуждает (про OCR, варианты), вырезаем. Если они были, перевод
    обычно стоит в конце, после рассуждений: берём последний абзац, который остался.
    Ответ из одной строки не трогаем: это и есть перевод (хоть бы и с именами латиницей)."""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if partial and len(lines) > 1 and target_lang not in LATIN_TARGETS:
        last = lines[-1]
        if not any(ch.isalpha() and not ch.isascii() for ch in last):
            lines = lines[:-1]  # дописывается строка латиницей: может оказаться рассуждением
    if len(lines) < 2:
        return lines[0] if partial and lines else text
    flags = [_thinking_line(ln, target_lang) for ln in lines]
    if not any(flags) or all(flags):
        return "\n".join(lines) if partial else text
    rest = [ln for ln, f in zip(lines, flags, strict=True) if not f]
    return rest[-1]


def drop_label(translation: str, source: str) -> str:
    """«Сакура Огами: Это все действия...» -> без подписи, если в оригинале её не было."""
    m = _LABEL_NAME.match(translation)
    if not m or ":" in source[: len(m.group(1)) + 12]:
        return translation
    words = m.group(1).split()
    if len(words) == 1 and not words[0][:1].isupper():
        return translation  # «смотри: ...» обычная речь, а не имя
    if all(w[:1].isupper() for w in words):
        return translation[m.end() :]
    return translation


_CYR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюяіїєґ", [
    "a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u", "f",
    "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya", "i", "yi", "ye", "g",
], strict=True))


def _latin(word: str) -> str:
    """Грубая латиница для сравнения имён: «Байякуя» -> bayyakuya, «Тогами» -> togami."""
    out = "".join(_CYR.get(ch, ch) for ch in word.lower())
    return re.sub(r"[^a-z]", "", out)


def drop_speaker_name(translation: str, speaker: str | None) -> str:
    """«Байякуя Тогами Судить о чужих ценностях...» -> без имени говорящего в начале.

    Модель иногда пишет имя перед репликой, даже без двоеточия. Убираем первые слова, если это
    имя говорящего (сравниваем по звучанию: Byakuya Togami ≈ Байякуя Тогами) и после них
    начинается фраза с заглавной."""
    from difflib import SequenceMatcher

    if not speaker:
        return translation
    names = speaker.split()
    if any(not w.isupper() for w in names):
        names = [w for w in names if not w.isupper() or len(w) < 3]  # «Toko Fukawa SPEAKING» -> Toko Fukawa
    speaker = " ".join(names)
    if not 1 <= len(names) <= 3:
        return translation
    words = translation.split(" ")
    n = len(names)
    if len(words) <= n:
        return translation
    head = words[:n]
    nxt = words[n].lstrip("\"'«“(")
    if not nxt or not (nxt[0].isupper() or nxt.startswith(("...", "…"))):
        return translation
    if not all(w[:1].isupper() and re.fullmatch(r"[^\W\d_]+(?:-[^\W\d_]+)*", w) for w in head):
        return translation  # «Макото, Кёко...»: с запятой это обращение, а не подпись
    a = "".join(_latin(w) for w in head)
    b = re.sub(r"[^a-z]", "", speaker.lower())
    if a and b and SequenceMatcher(None, a, b).ratio() >= 0.6:
        return " ".join(words[n:])
    return translation


_CORRECTION = re.compile(
    r"\(\s*(?:исправл\w*|поправл\w*|поправк\w*|correct\w*|fix\w*|oops|упс)\s*\)\s*"
    r"|\*\s*(?:исправл\w*|поправл\w*|correct\w*)\s*\*\s*",
    re.IGNORECASE,
)


def drop_self_correction(text: str) -> str:
    """«...на тебя это ничуdirect... (исправляю) Если бы ты...» -> только исправленный вариант.

    Модель без раздумий иногда сбивается и начинает фразу заново прямо в ответе."""
    parts = _CORRECTION.split(text)
    return parts[-1].lstrip() if len(parts) > 1 else text


_TAIL_NOISE = re.compile(r"(?<=[.!?…\"'»”’)])\s+[a-z|]{1,4}\s*$")


def drop_tail_noise(translation: str, target_lang: str = "ru") -> str:
    """«Это ты хочешь сказать? lij» -> без «lij»: стрелка окна диалога, которую OCR прочитал как буквы,
    а модель переписала из оригинала. В переводе не латиницей такой хвост не бывает настоящим."""
    if target_lang in LATIN_TARGETS:
        return translation
    return _TAIL_NOISE.sub("", translation)


# Имена, оставленные латиницей в русском переводе («виноват Byakuya»): берём то написание,
# которым модель уже писала это имя в прошлых репликах (Byakuya -> Бьякуя).
_LATIN_NAME = re.compile(r"(?<![\w'’-])[A-Z][a-z]{2,}(?:-[A-Za-z][a-z]+)?(?![\w'’-])")
_CYR_NAME = re.compile(r"(?<![\w-])[А-ЯЁЇІЄҐ][а-яёїієґ]+(?:-[А-ЯЁа-яё][а-яё]+)?(?![\w-])")
_CYRILLIC = re.compile(r"[а-яёїієґ]", re.IGNORECASE)
NAME_SIMILARITY = 0.75
_name_forms: dict[tuple[str, str], dict[str, int]] = {}
_learned: set[tuple[str, str]] = set()


def _english_word(token: str) -> bool:
    from lexoplay.wordlist_en import WORDS

    return token.lower() in WORDS


def _sounds(cyr: str, latin: str) -> float:
    from difflib import SequenceMatcher

    return SequenceMatcher(None, _latin(cyr), latin.lower()).ratio()


def learn_names(source: str, translation: str, target_lang: str) -> None:
    """Запомнить, как имена из оригинала записаны в переводе: Byakuya -> Бьякуя."""
    if target_lang in LATIN_TARGETS or not _CYRILLIC.search(translation):
        return
    key = (source, translation)
    if key in _learned:
        return
    if len(_learned) > 5000:
        _learned.clear()
    _learned.add(key)
    cands = set(_CYR_NAME.findall(translation))
    if not cands:
        return
    for name in set(_LATIN_NAME.findall(source)):
        if _english_word(name):
            continue
        best = max(cands, key=lambda c: _sounds(c, name))
        if _sounds(best, name) >= NAME_SIMILARITY:
            forms = _name_forms.setdefault((target_lang, name.lower()), {})
            forms[best] = forms.get(best, 0) + 1


def fix_latin_names(translation: str, target_lang: str = "ru") -> str:
    """Имя латиницей посреди кириллицы -> как его писали раньше (обычно именительный падеж:
    модель оставляет латиницу там, где имя не склоняется)."""
    if target_lang in LATIN_TARGETS or not _CYRILLIC.search(translation):
        return translation

    def sub(m: re.Match[str]) -> str:
        name = m.group(0)
        forms = _name_forms.get((target_lang, name.lower()))
        if not forms:
            return name
        return max(forms, key=lambda f: (round(_sounds(f, name), 2), forms[f]))

    return _LATIN_NAME.sub(sub, translation)


def tidy_saved(translation: str, *speakers: str | None, target_lang: str = "ru") -> str:
    """Перевод из кеша или истории: та же чистка, что у нового (старые версии её не знали)."""
    t = drop_self_correction(translation)
    for sp in speakers:
        t = drop_speaker_name(t, sp)
    return fix_latin_names(drop_tail_noise(t, target_lang), target_lang)


def split_reply(raw: str) -> tuple[str, str | None]:
    """Ответ модели → (перевод, исправленный оригинал после «@@» или None). Строка значений («##») не входит."""
    raw = split_hints(raw)[0]
    if SOURCE_MARK not in raw:
        return raw, None
    tr, src = raw.split(SOURCE_MARK, 1)
    src = " ".join(src.split()).strip(_QUOTES).strip()
    return tr, src or None


def pick_source(ocr_text: str, fixed: str | None) -> str:
    """Оригинал для показа: исправленный моделью, если это та же реплика, а не что-то своё."""
    from difflib import SequenceMatcher

    if not fixed:
        return ocr_text
    m = _SOURCE_LABEL.match(fixed)
    if m and ":" not in ocr_text[: len(m.group(1)) + 12]:
        fixed = fixed[m.end() :]  # «Narrator: But no matter...»: имя модель дописала сама
    a = re.sub(r"\W+", "", ocr_text.lower())
    b = re.sub(r"\W+", "", fixed.lower())
    if not a or not b or not 0.6 <= len(b) / len(a) <= 1.6:
        return ocr_text
    return fixed if SequenceMatcher(None, a, b).ratio() >= 0.6 else ocr_text


def clean_translation(
    text: str, speaker: str | None = None, *, partial: bool = False, target_lang: str = "ru", source: str | None = None
) -> str:
    """Ответ модели → строка для показа: без кавычек, подписи «Перевод:», имени говорящего.

    Если модель по привычке всё же ответила JSON или обернула ответ в теги, достаём перевод.
    Исправленный оригинал после «@@» сюда не попадает."""
    text = split_reply(text)[0]
    if partial:
        text = text.rstrip("@")  # «@@» дописывается
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
    t = drop_thinking(t, target_lang, partial=partial).strip()
    t = drop_self_correction(t)
    t = _LABEL.sub("", t)
    if source is not None and (speaker or _SOURCE_LABEL.match(source) is None):
        if partial and len(t) < 34 and ":" not in t and ":" not in source[:40] and "\n" not in t:
            return ""  # может оказаться подписью «Имя:»: покажем, когда станет ясно
        t = drop_label(t, source)
    if speaker:
        for prefix in (f"{speaker}:", f"{speaker.upper()}:"):
            if t.startswith(prefix):
                t = t[len(prefix) :].lstrip()
        t = drop_speaker_name(t, speaker)
    if not partial:
        t = drop_tail_noise(unwrap_quotes(t, source), target_lang)
    else:
        lead = (source or "").lstrip()[:1]
        if not (lead and lead in _QUOTES):  # оригинал начинается с кавычки: в переводе она своя, не обёртка
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
    think: модель немного думает (effort medium). Модель без effort переводит без thinking;
    модель, которая не даёт выключить thinking, переводит с effort=low.
    """

    def __init__(self, api_key: str, model: str, *, timeout: float = 20.0, think: bool = True) -> None:
        import anthropic  # лениво: тесты и самопроверка не тянут SDK без нужды

        self._anthropic = anthropic
        self._client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=1)
        self._model = model
        self._mode = "think" if think else "off"  # think | off | low
        self._last_used = 0.0

    def _params(self, req: TranslateRequest) -> dict:
        short = is_short(req.text)
        if short and len(req.context) > SHORT_CONTEXT:
            req = replace(req, context=req.context[-SHORT_CONTEXT:])  # «Huh?» не нужна вся сцена
        params: dict = {
            "model": self._model,
            "max_tokens": MAX_TOKENS,
            # постоянная часть промпта: Claude кеширует её, повторные запросы дешевле и быстрее
            "system": [{"type": "text", "text": build_system(req), "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": build_user(req)}],
        }
        if self._mode == "think":
            # короткую реплику обдумывать незачем: быстрее и дешевле, а перевод тот же
            params["output_config"] = {"effort": "low" if short else "medium"}
            params["max_tokens"] = MAX_TOKENS_THINKING
        elif self._mode == "off":
            params["thinking"] = {"type": "disabled"}
        else:
            params["output_config"] = {"effort": "low"}
        return params

    async def game_notes(
        self,
        game: str,
        lines: list[tuple[str | None, str]],
        speakers: list[str],
        *,
        source_lang: str = "en",
        target_lang: str = "ru",
    ) -> tuple[str | None, tuple[int, int] | None]:
        """Справка по игре (NOTES_PROMPT) и токены. Справка None: модель не знает эту игру."""
        a = self._anthropic
        params: dict = {
            "model": self._model,
            "max_tokens": NOTES_MAX_TOKENS,
            "system": NOTES_PROMPT.format(
                game=(game or "unknown").replace('"', "'"), source=lang_name(source_lang), target=lang_name(target_lang)
            ),
            "messages": [{"role": "user", "content": build_notes_user(game, lines, speakers)}],
        }
        if self._mode == "think":
            params["output_config"] = {"effort": "medium"}
        elif self._mode == "off":
            params["thinking"] = {"type": "disabled"}
        else:
            params["output_config"] = {"effort": "low"}
        for _attempt in range(2):
            try:
                msg = await self._client.messages.create(**params, timeout=NOTES_TIMEOUT_S)
                break
            except a.BadRequestError as exc:
                low = str(exc).lower()
                if "output_config" in params and ("effort" in low or "output_config" in low):
                    params.pop("output_config")  # модель без effort
                    continue
                if "thinking" in params and "thinking" in low:
                    params.pop("thinking")
                    params["output_config"] = {"effort": "low"}
                    continue
                raise friendly_error(a, exc, self._model) from exc
            except Exception as exc:
                raise friendly_error(a, exc, self._model) from exc
        else:
            raise TranslationError("Модель не приняла запрос справки по игре.")
        text = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text")
        return clean_notes(text), usage_of(msg)

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
        hinted = not (req.study and req.on_hints)
        async with self._client.messages.stream(**self._params(req)) as stream:
            async for chunk in stream.text_stream:
                buf += chunk
                if not hinted:
                    _rest, line, done = split_hints(buf)
                    if line is not None and done:
                        hinted = True
                        try:
                            req.on_hints(parse_hints(line))  # type: ignore[misc]
                        except Exception:
                            log.debug("on_hints упал", exc_info=True)
                if req.on_partial:
                    part = clean_translation(buf, req.speaker, partial=True, target_lang=req.target_lang, source=req.text)
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
                msg = str(exc).lower()
                if self._mode == "think" and ("effort" in msg or "output_config" in msg):
                    log.info("модель без effort, перевожу без thinking: %s", exc)
                    self._mode = "off"
                    continue
                if self._mode == "off" and "thinking" in msg:
                    log.info("модель не даёт выключить thinking, перехожу на effort=low: %s", exc)
                    self._mode = "low"
                    continue
                raise friendly_error(a, exc, self._model) from exc
            except Exception as exc:
                raise friendly_error(a, exc, self._model) from exc
            for c in req.context:
                learn_names(c.source, c.translation, req.target_lang)
            for k, v in req.glossary.items():
                learn_names(k, v, req.target_lang)
            translation = fix_start(
                req.text,
                fix_punctuation(req.text, clean_translation(raw, req.speaker, target_lang=req.target_lang, source=req.text)),
            )
            translation = fix_latin_names(translation, req.target_lang)
            if translation:
                learn_names(req.text, translation, req.target_lang)
                source = pick_source(req.text, split_reply(raw)[1])
                line = split_hints(raw)[1] if req.study else None
                hints = tuple(parse_hints(line)) if line is not None else None
                return TranslateResult(source=source, translation=translation, usage=usage, hints=hints)
            if stop == "refusal":
                raise TranslationError("Модель отказалась переводить эту реплику.")
            log.warning("пустой перевод: stop_reason=%s, ответ=%r", stop, raw[:200])
        raise TranslationError("Модель не прислала перевод, попробуй ещё раз.")

    async def hints(
        self,
        text: str,
        *,
        game: str = "",
        speaker: str | None = None,
        translation: str | None = None,
        level: str = "B1",
        room: int = 3,
        known: tuple[str, ...] | list[str] = (),
        source_lang: str = "en",
        target_lang: str = "ru",
    ) -> tuple[list[tuple[str, str]], tuple[int, int] | None]:
        """Учёба: подсказки к реплике, перевод которой уже есть (из истории, с сервера): (места, токены)."""
        a = self._anthropic
        user = [_line(speaker, text)]
        if translation:
            user.append(f"<translation>{translation}</translation>")
        user.append(study_tag(level, room, tuple(known)))
        params: dict = {
            "model": self._model,
            "max_tokens": HINTS_MAX_TOKENS,
            "system": HINTS_PROMPT.format(
                game=(game or "unknown").replace('"', "'"),
                source=lang_name(source_lang),
                study_rules=STUDY_RULES.format(target=lang_name(target_lang)),
            ),
            "messages": [{"role": "user", "content": "\n".join(user)}],
        }
        if self._mode == "low":
            params["output_config"] = {"effort": "low"}
        else:
            params["thinking"] = {"type": "disabled"}
        for _attempt in range(2):
            try:
                msg = await self._client.messages.create(**params)
                break
            except a.BadRequestError as exc:
                if "thinking" in params and "thinking" in str(exc).lower():
                    params.pop("thinking")
                    params["output_config"] = {"effort": "low"}
                    params["max_tokens"] = MAX_TOKENS
                    continue
                raise friendly_error(a, exc, self._model) from exc
            except Exception as exc:
                raise friendly_error(a, exc, self._model) from exc
        else:
            raise TranslationError("Модель не приняла запрос подсказок.")
        self._last_used = time.monotonic()
        raw = "".join(getattr(b, "text", "") for b in msg.content if getattr(b, "type", "") == "text")
        line = split_hints(raw)[1]
        if line is None and raw.strip():
            line = raw.strip().splitlines()[0]
        return parse_hints(line), usage_of(msg)

    async def translate_batch(
        self, reqs: list[TranslateRequest], on_partial: Callable[[int, str], None] | None = None
    ) -> tuple[list[TranslateResult | None], tuple[int, int] | None]:
        """Новые абзацы страницы одним запросом: промпт один раз на всех, а модель видит их вместе.

        (результаты по порядку, токены). None у абзаца: в ответе его не нашлось, переведи отдельно."""
        a = self._anthropic
        first = reqs[0]
        joined = replace(first, text="\n".join(r.text for r in reqs), speaker=None, on_partial=None)
        for _attempt in range(3):
            params = self._params(joined)
            params["messages"] = [{"role": "user", "content": build_batch_user(reqs)}]
            params["max_tokens"] = params["max_tokens"] + 500 * len(reqs)
            buf, shown = "", {}
            try:
                async with self._client.messages.stream(**params) as stream:
                    async for chunk in stream.text_stream:
                        buf += chunk
                        if on_partial is None or SOURCE_MARK in buf:
                            continue
                        parts, _src = split_batch(buf, len(reqs))
                        for i, part in enumerate(parts):
                            if not part:
                                continue
                            r = reqs[i]
                            text = clean_translation(part, r.speaker, partial=True, target_lang=r.target_lang, source=r.text)
                            if text and shown.get(i) != text:
                                shown[i] = text
                                try:
                                    on_partial(i, text)
                                except Exception:
                                    log.debug("on_partial упал", exc_info=True)
                    final = await stream.get_final_message()
                self._last_used = time.monotonic()
            except a.BadRequestError as exc:
                msg = str(exc).lower()
                if self._mode == "think" and ("effort" in msg or "output_config" in msg):
                    self._mode = "off"
                    continue
                if self._mode == "off" and "thinking" in msg:
                    self._mode = "low"
                    continue
                raise friendly_error(a, exc, self._model) from exc
            except Exception as exc:
                raise friendly_error(a, exc, self._model) from exc
            break
        else:
            raise TranslationError("Модель не приняла запрос перевода страницы.")
        for c in first.context:
            learn_names(c.source, c.translation, first.target_lang)
        for k, v in first.glossary.items():
            learn_names(k, v, first.target_lang)
        parts, sources = split_batch(buf, len(reqs))
        out: list[TranslateResult | None] = []
        for r, part, src in zip(reqs, parts, sources, strict=True):
            if not part:
                out.append(None)
                continue
            text = clean_translation(part, r.speaker, target_lang=r.target_lang, source=r.text)
            text = fix_latin_names(fix_start(r.text, fix_punctuation(r.text, text)), r.target_lang)
            if not text:
                out.append(None)
                continue
            learn_names(r.text, text, r.target_lang)
            out.append(TranslateResult(source=pick_source(r.text, src), translation=text))
        missing = sum(1 for x in out if x is None)
        if missing:
            log.info("страница одним запросом: %s из %s абзацев не нашлись в ответе", missing, len(reqs))
        return out, usage_of(final)


_PARA_MARK = re.compile(r"(?m)^\s*¶\s*(\d+)\s*[.:)]?\s*")


def build_batch_user(reqs: list[TranslateRequest]) -> str:
    """Несколько абзацев страницы в одном запросе: контекст общий, абзацы пронумерованы."""
    first = reqs[0]
    parts = []
    if first.context:
        parts.append("<earlier_lines>")
        for c in first.context:
            parts.append(_line(c.speaker, c.source))
            parts.append(f"<translation>{c.translation}</translation>")
        parts.append("</earlier_lines>")
    parts.append(
        "This time translate several paragraphs of the same page, in order. They follow each other, so read them "
        "together. Reply with each translation on its own line, starting with its number: ¶1 ..., ¶2 ... "
        "Then a line with @@ and the same paragraphs as the game really shows them, numbered the same way."
    )
    parts.append("Translate these paragraphs:")
    for i, r in enumerate(reqs, 1):
        who = f' speaker="{_attr(r.speaker)}"' if r.speaker else ""
        parts.append(f'<line n="{i}"{who}>{r.text}</line>')
    return "\n".join(parts)


def split_batch(raw: str, n: int) -> tuple[list[str | None], list[str | None]]:
    """Ответ «¶1 ... ¶2 ... @@ ¶1 ...» -> (переводы по номерам, исправленные оригиналы по номерам)."""
    tr_part, src_part = (raw.split(SOURCE_MARK, 1) + [""])[:2] if SOURCE_MARK in raw else (raw, "")

    def by_number(text: str) -> list[str | None]:
        out: list[str | None] = [None] * n
        marks = list(_PARA_MARK.finditer(text))
        for k, m in enumerate(marks):
            i = int(m.group(1)) - 1
            end = marks[k + 1].start() if k + 1 < len(marks) else len(text)
            chunk = text[m.end() : end].strip()
            if 0 <= i < n and chunk and out[i] is None:
                out[i] = chunk
        return out

    return by_number(tr_part), by_number(src_part)


def _short(exc: Exception) -> str:
    msg = getattr(exc, "message", None) or str(exc)
    return str(msg)[:160]


CACHE_WRITE_PRICE = 1.25  # запись в кеш промпта дороже обычного входа
CACHE_READ_PRICE = 0.1  # чтение из кеша: десятая часть цены входа


class PromptCacheStats:
    """Сколько входа пришло из кеша промпта Anthropic. Кеш работает, только если постоянная часть
    промпта не короче порога модели: по этим цифрам видно, срабатывает ли он (и в отчёте тоже)."""

    def __init__(self) -> None:
        self.requests = 0
        self.plain = 0  # вход без кеша
        self.read = 0  # из кеша
        self.write = 0  # записано в кеш

    def add(self, plain: int, read: int, write: int) -> None:
        self.requests += 1
        self.plain += plain
        self.read += read
        self.write += write

    @property
    def hit_share(self) -> float:
        total = self.plain + self.read + self.write
        return self.read / total if total else 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "requests": self.requests,
            "input_plain": self.plain,
            "input_cache_read": self.read,
            "input_cache_write": self.write,
            "hit_share": round(self.hit_share, 3),
        }


PROMPT_CACHE = PromptCacheStats()


def usage_of(message: object) -> tuple[int, int] | None:
    """(вход, выход) в токенах по цене обычного входа: прочитанное из кеша стоит 0.1, записанное 1.25.

    Так счётчик расходов не завышает цену, когда кеш работает, и не занижает, когда пишет."""
    u = getattr(message, "usage", None)
    if u is None:
        return None
    try:
        plain = int(getattr(u, "input_tokens", 0) or 0)
        read = int(getattr(u, "cache_read_input_tokens", 0) or 0)
        write = int(getattr(u, "cache_creation_input_tokens", 0) or 0)
        out = int(getattr(u, "output_tokens", 0) or 0)
    except (TypeError, ValueError):
        return None
    PROMPT_CACHE.add(plain, read, write)
    log.info("токены: вход %s, из кеша промпта %s, в кеш %s, выход %s", plain, read, write, out)
    return plain + round(write * CACHE_WRITE_PRICE) + round(read * CACHE_READ_PRICE), out


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
