"""Слова новеллы: сколько её текста игрок уже понимает и какие слова выучить перед каждой частью.

Из текста (novel.Script) считаем словарные формы по частям и храним только счётчики, без самих реплик:
data_dir()/novels/<exe>.json. Готовность к части: доля слов этой части, которые игрок уже знает
(частые для его уровня, уже в Lexo или отмеченные «уже знаю»). Колода к части: 10–15 незнакомых слов,
которые чаще всего встретятся в ней и дальше по новелле. Имена (слова, которые ни разу не встретились
со строчной буквы) и междометия не считаем.
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

log = logging.getLogger(__name__)

VERSION = 1
DECK_SIZE = 12
# уровень игрока (study_level) -> с какого места в частотном списке слова для него незнакомые
EDGES = {
    "a2": rarity.COMMON,
    "b1": rarity.LEVELS["b1"],
    "b2": rarity.LEVELS["b2"],
    "c1": rarity.LEVELS["c1"],
}
# японские обращения в английских переводах (Battler-kun, nii-san): учить их как слова незачем
HONORIFICS = frozenset(
    "san kun chan sama senpai sempai sensei nii nee onii onee dono tan chin aniki aneki niisan neesan "
    "oniisan oneesan oniichan oneechan nyan desu".split()
)
_TOKEN = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*(?:-[A-Za-z]+(?:['’][A-Za-z]+)*)*")


@dataclass(slots=True)
class PartStat:
    title: str
    tokens: int  # слов в части (без имён)
    base: int  # из них самых частых (rank < rarity.COMMON) и междометий: их знает любой уровень
    counts: dict[str, int]  # остальные: словарная форма -> сколько раз


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
    taken: set[str] = field(default_factory=set)  # уже отправлены в колоду отсюда
    skipped: set[str] = field(default_factory=set)  # «уже знаю»

    @property
    def words(self) -> int:
        return sum(p.tokens for p in self.parts)

    def total(self, lemma: str) -> int:
        return sum(p.counts.get(lemma, 0) for p in self.parts)


@dataclass(slots=True)
class NovelView:
    """Что показать в окне LexoPlay про слова новеллы."""

    state: str = "none"  # none (ещё не искали) | scanning | ready | error
    summary: str = ""  # «Ren'Py · 412 тыс. слов · 4 акта · понятно 96%» или что нашлось в папке
    error: str = ""
    folder: str = ""
    note: str = ""
    parts: list[tuple[str, float]] = field(default_factory=list)  # (название, доля знакомых слов)
    can_read: bool = True  # движок известен как нечитаемый: False, причина в error


@dataclass(frozen=True, slots=True)
class DeckWord:
    lemma: str
    here: int  # раз в этой части
    total: int  # раз во всей новелле


@dataclass(frozen=True, slots=True)
class Deck:
    part: int
    words: list[DeckWord]
    now: float  # доля слов части, которые игрок знает сейчас
    after: float  # если выучит колоду
    unknown: int  # незнакомых словарных форм в части (без одноразовых)

    @property
    def hits(self) -> int:
        """Сколько раз слова колоды встретятся в этой части."""
        return sum(d.here for d in self.words)


def count(script: Script) -> list[PartStat]:
    """Счётчики словарных форм по частям. Имена отсеиваем: слово считается, если хоть раз было со строчной."""
    raw: list[Counter[str]] = []
    lower: set[str] = set()
    for part in script.parts:
        c: Counter[str] = Counter()
        for line in part.lines:
            for compound in _TOKEN.findall(line):
                pieces = compound.replace("’", "'").split("-")
                named = (
                    len(pieces) > 1 and compound[0].isupper()
                )  # Battler-kun, Onii-chan: часть имени
                for tok in pieces:
                    c[tok] += 1
                    if tok[0].islower() and not named:
                        lower.add(tok.lower())
        raw.append(c)
    cache: dict[str, str | None] = {}

    def lemma_of(w: str) -> str | None:  # None: в «базу» (частое или междометие)
        if w not in cache:
            r = rarity.rank(w)
            if (
                rarity._junk(w)
                or w in HONORIFICS
                or (r is not None and r < rarity.COMMON)
                or len(w) < 3
            ):
                cache[w] = None
            elif (
                r is None
            ):  # нет в частотном списке: lingering -> linger, если linger есть в самой новелле
                seen = sorted(
                    (b for b in rarity.bases(w) if b in lower and rarity.rank(b) is None), key=len
                )
                cache[w] = seen[0] if seen else w
            else:
                cache[w] = rarity.lemma(w)
        return cache[w]

    out: list[PartStat] = []
    for part, c in zip(script.parts, raw, strict=True):
        tokens = base = 0
        counts: Counter[str] = Counter()
        for tok, n in c.items():
            w = tok.lower()
            if w not in lower and not (rarity.bases(w) & lower):
                continue  # Hisao, Lilly, Tokyo: имена и названия (ни разу со строчной, ни сами, ни их формы)
            tokens += n
            lem = lemma_of(w)
            if lem is None:
                base += n
            else:
                counts[lem] += n
        out.append(PartStat(part.title, tokens, base, dict(counts)))
    return out


def build(exe: str, title: str, folder: Path, script: Script) -> Novel:
    return Novel(
        exe=exe,
        title=title,
        folder=str(folder),
        engine=script.engine.key,
        engine_name=script.engine.name,
        note=script.note,
        parts=count(script),
        scanned_at=time.time(),
    )


def knows(novel: Novel, level: str, known: Callable[[str], bool]) -> Callable[[str], bool]:
    """Знает ли игрок словарную форму: частая для уровня, уже в Lexo, «уже знаю» или уже в колоде отсюда."""
    edge = EDGES.get(level, EDGES["b1"])
    cache: dict[str, bool] = {}

    def k(lem: str) -> bool:
        if lem not in cache:
            r = rarity.rank(lem)
            cache[lem] = (
                (r is not None and r < edge)
                or lem in novel.taken
                or lem in novel.skipped
                or known(lem)
            )
        return cache[lem]

    return k


def coverage(
    part: PartStat, k: Callable[[str], bool], extra: set[str] | frozenset[str] = frozenset()
) -> float:
    if not part.tokens:
        return 1.0
    have = part.base + sum(n for lem, n in part.counts.items() if lem in extra or k(lem))
    return have / part.tokens


def deck(
    novel: Novel, index: int, level: str, known: Callable[[str], bool], size: int = DECK_SIZE
) -> Deck:
    """Колода к части index: незнакомые слова, которые важнее всего для неё (и встретятся потом ещё)."""
    part = novel.parts[index]
    k = knows(novel, level, known)
    later = novel.parts[index:]
    cands: list[DeckWord] = []
    for lem, here in part.counts.items():
        if k(lem) or not _learnable(lem):
            continue
        total = sum(p.counts.get(lem, 0) for p in later)
        cands.append(DeckWord(lem, here, total))
    # сначала слова, которые встретятся хотя бы трижды: одноразовое слово учить незачем
    cands.sort(
        key=lambda d: (d.total >= 3, d.here >= 2, d.here * 2 + d.total, d.lemma), reverse=True
    )
    picked = cands[:size]
    unknown = sum(1 for d in cands if d.here >= 2)
    return Deck(
        index, picked, coverage(part, k), coverage(part, k, {d.lemma for d in picked}), unknown
    )


def _learnable(lem: str) -> bool:
    return len(lem) >= 3 and "'" not in lem and lem.isalpha()


def readiness(novel: Novel, level: str, known: Callable[[str], bool]) -> list[float]:
    k = knows(novel, level, known)
    return [coverage(p, k) for p in novel.parts]


# ------------------------------------------------------------------ хранение
def _dir() -> Path:
    d = data_dir() / "novels"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(exe: str) -> Path:
    safe = re.sub(r"[^\w.\-]+", "_", exe.lower()) or "game"
    return _dir() / f"{safe}.json"


def save(novel: Novel) -> None:
    data = {
        "version": VERSION,
        "exe": novel.exe,
        "title": novel.title,
        "folder": novel.folder,
        "engine": novel.engine,
        "engine_name": novel.engine_name,
        "note": novel.note,
        "scanned_at": novel.scanned_at,
        "taken": sorted(novel.taken),
        "skipped": sorted(novel.skipped),
        "parts": [
            {"title": p.title, "tokens": p.tokens, "base": p.base, "counts": p.counts}
            for p in novel.parts
        ],
    }
    path = _path(novel.exe)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


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
                PartStat(p["title"], int(p["tokens"]), int(p["base"]), dict(p["counts"]))
                for p in data["parts"]
            ],
            scanned_at=float(data.get("scanned_at", 0)),
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


def summary(novel: Novel, level: str, known: Callable[[str], bool]) -> str:
    """«Ren'Py · 412 тыс. слов · 4 части · понятно 86%»."""
    ready = readiness(novel, level, known)
    total = novel.words or 1
    share = sum(r * p.tokens for r, p in zip(ready, novel.parts, strict=True)) / total
    bits = [novel.engine_name, f"{thousands(novel.words)} слов"]
    if len(novel.parts) > 1:
        bits.append(parts_word(novel))
    bits.append(f"понятно {round(share * 100)}%")
    return " · ".join(bits)
