"""Слова новеллы: какие сложные слова и выражения в ней встречаются часто и сколько текста игрок уже понимает.

Из текста (novel.Script) считаем словарные формы и фразовые глаголы по частям и храним только счётчики,
без самих реплик: data_dir()/novels/<exe>.json. Уровень (A1–C2) каждому слову ставит модель по списку
слов, без текста игры, один раз на слово (word_levels.json общий для всех новелл); она же отсеивает имена
и не-слова. Игроку показываем общий список на всю новеллу: слова выше его уровня, которые встретятся
хотя бы N раз, самые частые сверху. Порядок, в котором слово впервые встречается, нужен только колоде:
слова уходят в Lexo так, чтобы раньше учились те, что раньше понадобятся.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from lexoplay import rarity
from lexoplay.config import data_dir
from lexoplay.novel import Script
from lexoplay.words import IRREGULAR, STOPWORDS

log = logging.getLogger(__name__)

VERSION = 3  # 3: фразовые глаголы, порядок появления, уровни слов от модели
LEVELS = ("A1", "A2", "B1", "B2", "C1", "C2")
NOT_WORD = ("NAME", "X")  # имя или не слово: не учим и считаем «понятным»
USER_LEVEL = {"a2": "A2", "b1": "B1", "b2": "B2", "c1": "C1"}  # study_level -> CEFR
COUNTS = (4, 5, 8, 12, 20)  # «встречается от N раз»
MIN_COUNT = COUNTS[0]  # реже этого слово не оцениваем и не показываем
MAX_LIST = 400  # больше слов за раз не разобрать: порог поднимается сам
# слов в порции: Lexo вводит по 20 новых карточек в день, порция это примерно 3 дня. Большая часть
# (акт на 180 слов) делится на порции по порядку появления: сначала слова её начала
PORTION = 60
# уровень игрока -> с какого места в частотном списке слова для него незнакомые (пока модель не оценила)
EDGES = {"a2": rarity.COMMON, "b1": rarity.LEVELS["b1"], "b2": rarity.LEVELS["b2"], "c1": rarity.LEVELS["c1"]}
# японские обращения в английских переводах (Battler-kun, nii-san): учить их как слова незачем
HONORIFICS = frozenset(
    "san kun chan sama senpai sempai sensei nii nee onii onee dono tan chin aniki aneki niisan neesan "
    "oniisan oneesan oniichan oneechan nyan desu".split()
)
# частицы фразовых глаголов: verb + одна из них (make out, end up, pull off)
PARTICLES = frozenset("up out off down over away back through around along apart aside across forward".split())
# перед частицей не глагол: местоимения, предлоги, связки (pick it up даёт «it up», это не выражение)
NOT_VERB = STOPWORDS - frozenset("get got go going gone come do does did doing done have has had having let".split())
NAME_SHARE = 0.1  # слово со строчной реже, чем в каждом десятом случае: имя (Shizune, Hisao, Tokyo)
_TOKEN = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*(?:-[A-Za-z]+(?:['’][A-Za-z]+)*)*")
_BREAK = re.compile(r"[.!?…,;:—()\"]")


@dataclass(slots=True)
class PartStat:
    title: str
    tokens: int  # слов в части (без имён)
    base: int  # из них самых частых (rank < rarity.COMMON) и междометий: их знает любой уровень
    counts: dict[str, int]  # остальные: словарная форма -> сколько раз
    phrases: dict[str, int] = field(default_factory=dict)  # фразовые глаголы: «end up» -> сколько раз


@dataclass(slots=True)
class Novel:
    exe: str
    title: str
    folder: str
    engine: str  # ключ движка
    engine_name: str
    note: str
    parts: list[PartStat]
    scanned_at: float
    order: dict[str, int] = field(default_factory=dict)  # слово или выражение -> номер слова, где оно впервые
    levels: dict[str, str] = field(default_factory=dict)  # оценка модели: A1..C2, NAME, X
    taken: set[str] = field(default_factory=set)  # уже отправлены в колоду отсюда
    skipped: set[str] = field(default_factory=set)  # «уже знаю»

    @property
    def words(self) -> int:
        return sum(p.tokens for p in self.parts)

    def total(self, item: str) -> int:
        return sum(p.counts.get(item, 0) + p.phrases.get(item, 0) for p in self.parts)

    def totals(self) -> dict[str, int]:
        """Слова и выражения -> сколько раз во всей новелле."""
        out: Counter[str] = Counter()
        for p in self.parts:
            out.update(p.counts)
            out.update(p.phrases)
        return dict(out)


@dataclass(frozen=True, slots=True)
class Candidate:
    """Строка списка: слово или выражение, его уровень и сколько раз встретится."""

    text: str
    level: str
    total: int
    order: int
    phrase: bool
    part: int = 0  # в какой части встречается впервые: к ней его и учить


@dataclass(slots=True)
class NovelView:
    """Что показать в окне LexoPlay про слова новеллы."""

    state: str = "none"  # none (ещё не искали) | scanning | ready | error
    summary: str = ""  # «Ren'Py · 462 тыс. слов · 4 акта · знаешь 88%» или что нашлось в папке
    error: str = ""
    folder: str = ""
    note: str = ""
    # (название части, доля знакомых слов, доля знакомых вместе со словами в колоде)
    parts: list[tuple[str, float, float]] = field(default_factory=list)
    can_read: bool = True  # движок известен как нечитаемый: False, причина в error
    busy: str = ""  # "" | scan | level | send
    progress: str = ""  # «Модель оценивает слова: 600 из 2400»
    message: str = ""  # итог последней оценки или отправки (или ошибка)
    leveled: bool = False  # модель оценила слова: список можно показывать
    user_level: str = "B1"


# ------------------------------------------------------------------ подсчёт
def count(script: Script) -> tuple[list[PartStat], dict[str, int]]:
    """Счётчики по частям и порядок появления. Имена отсеиваем: они почти всегда с заглавной."""
    raw: list[list[list[tuple[str, bool]]]] = []  # часть -> строка -> (слово, начало фразы)
    every: Counter[str] = Counter()
    low: Counter[str] = Counter()
    for part in script.parts:
        rows: list[list[tuple[str, bool]]] = []
        for line in part.lines:
            row: list[tuple[str, bool]] = []
            for m in _TOKEN.finditer(line):
                compound = m.group(0)
                pieces = compound.replace("’", "'").split("-")
                named = len(pieces) > 1 and compound[0].isupper()  # Battler-kun, Onii-chan: часть имени
                # слово сразу после знака препинания: фразовый глагол через него не тянем
                broken = bool(_BREAK.search(line[max(0, m.start() - 3) : m.start()])) or m.start() == 0
                for i, tok in enumerate(pieces):
                    w = tok.lower()
                    row.append((w, broken or i > 0 or len(pieces) > 1))
                    every[w] += 1
                    if tok[0].islower() and not named:
                        low[w] += 1
            rows.append(row)
        raw.append(rows)
    lower = {w for w, n in low.items() if n >= every[w] * NAME_SHARE}
    cache: dict[str, str | None] = {}

    def word_ok(w: str) -> bool:
        return w in lower or bool(rarity.bases(w) & lower)

    def lemma_of(w: str) -> str | None:  # None: в «базу» (частое или междометие)
        if w not in cache:
            r = rarity.rank(w)
            if rarity._junk(w) or w in HONORIFICS or (r is not None and r < rarity.COMMON) or len(w) < 3:
                cache[w] = None
            elif r is None:  # нет в частотном списке: lingering -> linger, если linger есть в самой новелле
                seen = sorted((b for b in rarity.bases(w) if b in lower and rarity.rank(b) is None), key=len)
                cache[w] = seen[0] if seen else w
            else:
                cache[w] = rarity.lemma(w)
        return cache[w]

    def verb_of(w: str) -> str | None:
        if w in IRREGULAR:
            return IRREGULAR[w]
        if w in NOT_VERB or rarity._junk(w) or len(w) < 2 or rarity.rank(w) is None:
            return None
        return rarity.lemma(w)

    out: list[PartStat] = []
    order: dict[str, int] = {}
    pos = 0
    for part, rows in zip(script.parts, raw, strict=True):
        tokens = base = 0
        counts: Counter[str] = Counter()
        phrases: Counter[str] = Counter()
        for row in rows:
            prev: str | None = None
            for w, broken in row:
                pos += 1
                if not word_ok(w):
                    prev = None
                    continue  # Hisao, Lilly, Tokyo: имена и названия
                tokens += 1
                if w in PARTICLES and prev is not None and not broken:
                    verb = verb_of(prev)
                    if verb is not None and verb not in PARTICLES:
                        phrase = f"{verb} {w}"
                        phrases[phrase] += 1
                        order.setdefault(phrase, pos)
                lem = lemma_of(w)
                if lem is None:
                    base += 1
                else:
                    counts[lem] += 1
                    order.setdefault(lem, pos)
                prev = w
        out.append(PartStat(part.title, tokens, base, dict(counts), dict(phrases)))
    # порядок храним только для того, что может попасть в список
    totals: Counter[str] = Counter()
    for p in out:
        totals.update(p.counts)
        totals.update(p.phrases)
    order = {k: v for k, v in order.items() if totals[k] >= MIN_COUNT}
    for p in out:  # редкие выражения не нужны и только раздувают файл
        p.phrases = {k: n for k, n in p.phrases.items() if totals[k] >= MIN_COUNT}
    return out, order


def build(exe: str, title: str, folder: Path, script: Script) -> Novel:
    parts, order = count(script)
    return Novel(
        exe=exe,
        title=title,
        folder=str(folder),
        engine=script.engine.key,
        engine_name=script.engine.name,
        note=script.note,
        parts=parts,
        scanned_at=time.time(),
        order=order,
    )


def _learnable(item: str) -> bool:
    if " " in item:
        return True
    return len(item) >= 3 and "'" not in item and item.isalpha()


def to_level(novel: Novel) -> list[str]:
    """Что ещё надо оценить модели: слова и выражения, которые встречаются хотя бы MIN_COUNT раз."""
    totals = novel.totals()
    out = [k for k, n in totals.items() if n >= MIN_COUNT and _learnable(k) and k not in novel.levels]
    out.sort(key=lambda k: -totals[k])
    return out


def level_index(level: str) -> int:
    return LEVELS.index(level) if level in LEVELS else -1


def _above(level: str, edge: str) -> bool:
    return level in LEVELS and level_index(level) > level_index(edge)


# ------------------------------------------------------------------ знает ли игрок
def knows(novel: Novel, level: str) -> Callable[[str], bool]:
    """Знает ли игрок слово сам: по оценке модели (не выше его уровня), имя или не слово, «уже знаю».
    Пока модель не оценила, по частотному списку."""
    user = USER_LEVEL.get(level, "B1")
    edge = EDGES.get(level, EDGES["b1"])

    def k(lem: str) -> bool:
        if lem in novel.skipped:
            return True
        lv = novel.levels.get(lem)
        if lv is not None:
            return lv in NOT_WORD or not _above(lv, user)
        r = rarity.rank(lem)
        return r is not None and r < edge

    return k


def readiness(novel: Novel, level: str, known: Callable[[str], bool]) -> list[tuple[float, float]]:
    """По частям: (доля слов, которые игрок знает, доля вместе со словами в колоде)."""
    k = knows(novel, level)
    out = []
    for p in novel.parts:
        if not p.tokens:
            out.append((1.0, 1.0))
            continue
        have = deck = p.base
        for lem, n in p.counts.items():
            if k(lem):
                have += n
                deck += n
            elif lem in novel.taken or known(lem):
                deck += n
        out.append((have / p.tokens, deck / p.tokens))
    return out


def summary(novel: Novel, level: str, known: Callable[[str], bool]) -> str:
    """«Ren'Py · 462 тыс. слов · 4 акта · знаешь 88%»."""
    ready = readiness(novel, level, known)
    total = novel.words or 1
    share = sum(r[0] * p.tokens for r, p in zip(ready, novel.parts, strict=True)) / total
    bits = [novel.engine_name, f"{thousands(novel.words)} слов"]
    if len(novel.parts) > 1:
        bits.append(parts_word(novel))
    bits.append(f"знаешь {round(share * 100)}%")
    return " · ".join(bits)


# ------------------------------------------------------------------ список
def candidates(novel: Novel, min_level: str, min_count: int, known: Callable[[str], bool]) -> list[Candidate]:
    """Слова и выражения не ниже min_level, которые встретятся хотя бы min_count раз.
    Без «уже знаю», без отправленных отсюда и без тех, что уже есть в Lexo. Самые частые сверху."""
    edge = LEVELS[max(0, level_index(min_level) - 1)] if level_index(min_level) > 0 else ""
    out: list[Candidate] = []
    for item, total in novel.totals().items():
        if total < min_count or item in novel.skipped or item in novel.taken:
            continue
        lv = novel.levels.get(item)
        if lv is None or lv in NOT_WORD or (edge and not _above(lv, edge)):
            continue
        if known(item):
            continue
        part = next((i for i, p in enumerate(novel.parts) if item in p.counts or item in p.phrases), 0)
        out.append(Candidate(item, lv, total, novel.order.get(item, 1 << 30), " " in item, part))
    out.sort(key=lambda c: (-c.total, c.order))
    return out


def default_count(novel: Novel, min_level: str, known: Callable[[str], bool]) -> int:
    """Порог «встречается от N раз»: примерно раз на 100 тыс. слов текста (раз в 6–7 часов чтения),
    но не меньше 4. Если слов выходит больше MAX_LIST, порог поднимается."""
    want = min(15, max(MIN_COUNT, round(novel.words / 100_000)))
    options = [c for c in COUNTS if c >= want] or [COUNTS[-1]]
    for c in options:
        if len(candidates(novel, min_level, c, known)) <= MAX_LIST:
            return c
    return options[-1]


def next_level(level: str) -> str:
    """С какого уровня слова игроку уже сложные: на ступень выше его (B1 -> B2)."""
    user = USER_LEVEL.get(level, "B1")
    return LEVELS[min(len(LEVELS) - 1, level_index(user) + 1)]


# ------------------------------------------------------------------ хранение
def _dir() -> Path:
    d = data_dir() / "novels"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(exe: str) -> Path:
    safe = re.sub(r"[^\w.\-]+", "_", exe.lower()) or "game"
    return _dir() / f"{safe}.json"


def _write(path: Path, data: object) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def save(novel: Novel) -> None:
    _write(
        _path(novel.exe),
        {
            "version": VERSION,
            "exe": novel.exe,
            "title": novel.title,
            "folder": novel.folder,
            "engine": novel.engine,
            "engine_name": novel.engine_name,
            "note": novel.note,
            "scanned_at": novel.scanned_at,
            "order": novel.order,
            "levels": novel.levels,
            "taken": sorted(novel.taken),
            "skipped": sorted(novel.skipped),
            "parts": [
                {"title": p.title, "tokens": p.tokens, "base": p.base, "counts": p.counts, "phrases": p.phrases}
                for p in novel.parts
            ],
        },
    )


def load(exe: str) -> Novel | None:
    path = _path(exe)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != VERSION:
            return None
        return Novel(
            exe=data["exe"],
            title=data["title"],
            folder=data["folder"],
            engine=data["engine"],
            engine_name=data["engine_name"],
            note=data.get("note", ""),
            parts=[
                PartStat(p["title"], int(p["tokens"]), int(p["base"]), dict(p["counts"]), dict(p.get("phrases", {})))
                for p in data["parts"]
            ],
            scanned_at=float(data.get("scanned_at", 0)),
            order={str(k): int(v) for k, v in data.get("order", {}).items()},
            levels={str(k): str(v) for k, v in data.get("levels", {}).items()},
            taken=set(data.get("taken", [])),
            skipped=set(data.get("skipped", [])),
        )
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError):
        log.warning("слова новеллы %s не прочитались", exe, exc_info=True)
        return None


def forget(exe: str) -> None:
    try:
        _path(exe).unlink()
    except OSError:
        pass


def _levels_path() -> Path:
    return _dir() / "word_levels.json"


def known_levels() -> dict[str, str]:
    """Уровни, которые модель уже ставила в других новеллах: второй раз за них не платим."""
    try:
        data = json.loads(_levels_path().read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def remember_levels(levels: dict[str, str]) -> None:
    """Имена (NAME) общими не делаем: в другой новелле это может быть обычное слово."""
    allw = known_levels()
    allw.update({k: v for k, v in levels.items() if v != "NAME"})
    try:
        _write(_levels_path(), allw)
    except OSError:
        log.warning("уровни слов не сохранились", exc_info=True)


# ------------------------------------------------------------------ слова для людей
def thousands(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} млн".replace(".0 ", " ")
    if n >= 1000:
        return f"{round(n / 1000)} тыс."
    return str(n)


_FORMS = {
    "Акт": ("акт", "акта", "актов"),
    "Глава": ("глава", "главы", "глав"),
    "Эпизод": ("эпизод", "эпизода", "эпизодов"),
    "День": ("день", "дня", "дней"),
    "Часть": ("часть", "части", "частей"),
}


def plural(n: int, forms: tuple[str, str, str]) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} {forms[0]}"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} {forms[1]}"
    return f"{n} {forms[2]}"


def parts_word(novel: Novel) -> str:
    kind = novel.parts[0].title.split()[0] if novel.parts else "Часть"
    return plural(len(novel.parts), _FORMS.get(kind, _FORMS["Часть"]))
