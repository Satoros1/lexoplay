"""Прогресс: реплики, слова и время по дням, серия дней подряд, цель на день, рекорды.

Считается локально (таблица stats): реплика засчитывается, когда переведена впервые в
сессии, слово, когда сохранено, время, пока игра на экране. Рекорды по сессиям и «когда
ты играешь» берутся из истории реплик (timeline.py).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from lexoplay.store import Store
from lexoplay.timeline import PlaySession, split_sessions

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
WORDS_PER_PAGE = 250
ACTIVITY_DAYS = 53 * 7  # тепловая карта: до года, сколько влезет по ширине

# группы частей речи для «что ты сохраняешь»: (ключ, подпись)
POS_GROUPS = [
    ("noun", "существительные"),
    ("verb", "глаголы"),
    ("adj", "прилагательные"),
    ("adv", "наречия"),
    ("phrase", "фразы и идиомы"),
    ("other", "другое"),
]


@dataclass(frozen=True, slots=True)
class DayStat:
    day: date
    lines: int = 0
    words: int = 0
    seconds: int = 0

    @property
    def label(self) -> str:
        return WEEKDAYS[self.day.weekday()]

    def value(self, metric: str) -> int:
        return {"lines": self.lines, "words": self.words, "seconds": self.seconds}[metric]


@dataclass(frozen=True, slots=True)
class Records:
    best_lines: tuple[date, int] | None = None  # день, когда прочитал больше всего реплик
    best_words: tuple[date, int] | None = None  # и сохранил больше всего слов
    best_streak: int = 0
    longest: PlaySession | None = None  # самая долгая сессия
    biggest: PlaySession | None = None  # больше всего реплик за сессию


@dataclass(frozen=True, slots=True)
class GameStat:
    exe: str
    lines: int
    words: int
    seconds: int
    top_speaker: str | None = None

    @property
    def words_per_hour(self) -> float | None:
        """Новых слов за час игры, если играл хотя бы 20 минут."""
        return self.words * 3600 / self.seconds if self.seconds >= 20 * 60 else None


@dataclass(frozen=True, slots=True)
class Progress:
    today: DayStat
    week: list[DayStat]  # 7 дней, последний = сегодня
    streak: int  # дней подряд с чтением или словами (сегодня или вчера включительно)
    by_game: list[tuple[str, int, int, int]]  # за 7 дней: exe, реплик, слов, секунд
    totals: tuple[int, int, int]
    goal: int
    activity: list[DayStat] = field(default_factory=list)  # для тепловой карты, последний = сегодня
    records: Records = field(default_factory=Records)
    hours: list[int] = field(default_factory=lambda: [0] * 24)  # реплик по часу суток
    pos: list[tuple[str, str, int]] = field(default_factory=list)  # (ключ, подпись, слов)
    words_read: int = 0  # английских слов в прочитанных репликах

    @property
    def pages_read(self) -> int:
        """Те же слова в страницах обычной книги (около 250 слов на страницу)."""
        return round(self.words_read / WORDS_PER_PAGE)

    @property
    def goal_done(self) -> bool:
        return self.today.words >= self.goal > 0

    @property
    def words_per_hour(self) -> float | None:
        _lines, words, seconds = self.totals
        return words * 3600 / seconds if seconds >= 30 * 60 else None


def streak_from(active_days: list[str], today: date) -> int:
    days = {date.fromisoformat(d) for d in active_days}
    cur = today if today in days else today - timedelta(days=1)
    n = 0
    while cur in days:
        n += 1
        cur -= timedelta(days=1)
    return n


def longest_streak(active_days: list[str]) -> int:
    best = run = 0
    prev: date | None = None
    for d in sorted({date.fromisoformat(x) for x in active_days}):
        run = run + 1 if prev is not None and d - prev == timedelta(days=1) else 1
        best = max(best, run)
        prev = d
    return best


def pos_group(pos: str, multiword: bool) -> str:
    p = pos.strip().lower()
    if multiword or any(k in p for k in ("phras", "idiom", "expression")):
        return "phrase"
    for key, prefixes in (("noun", ("noun", "proper")), ("verb", ("verb",)), ("adj", ("adj",)), ("adv", ("adv",))):
        if p.startswith(prefixes):
            return key
    return "other"


def pos_breakdown(store: Store) -> list[tuple[str, str, int]]:
    counts: dict[str, int] = {}
    for pos, multi, n in store.words_pos():
        g = pos_group(pos, multi)
        counts[g] = counts.get(g, 0) + n
    out = [(key, name, counts[key]) for key, name in POS_GROUPS if counts.get(key)]
    out.sort(key=lambda x: (x[0] == "other", -x[2]))  # «другое» всегда в конце
    return out


def hours(store: Store) -> list[int]:
    out = [0] * 24
    for ts in store.line_times():
        out[datetime.fromtimestamp(ts).hour] += 1
    return out


def records(store: Store, sessions: list[PlaySession] | None = None) -> Records:
    def best(metric: str) -> tuple[date, int] | None:
        got = store.stats_best_day(metric)
        return (date.fromisoformat(got[0]), got[1]) if got else None

    if sessions is None:
        sessions = split_sessions(store.history_events())
    with_lines = [s for s in sessions if s.lines]
    return Records(
        best_lines=best("lines"),
        best_words=best("words"),
        best_streak=longest_streak(store.stats_active_days(limit=100_000)),
        longest=max(with_lines, key=lambda s: s.seconds, default=None),
        biggest=max(with_lines, key=lambda s: s.lines, default=None),
    )


def activity(store: Store, now: date, days: int = ACTIVITY_DAYS) -> list[DayStat]:
    start = now - timedelta(days=days - 1)
    raw = store.stats_days(start.isoformat())
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        lines, words, seconds = raw.get(d.isoformat(), (0, 0, 0))
        out.append(DayStat(d, lines, words, seconds))
    return out


def games(store: Store, since: date | None) -> list[GameStat]:
    """Игры за период (None = за всё время): больше всего реплик сверху."""
    rows = store.stats_by_game(since.isoformat() if since else "0000-00-00")
    out = []
    for exe, lines, words, seconds in rows:
        top = store.speaker_counts(exe, 1)
        out.append(GameStat(exe, lines, words, seconds, top[0][0] if top else None))
    return out


def progress(store: Store, goal: int, today: Callable[[], date] = date.today) -> Progress:
    now = today()
    act = activity(store, now)
    week = act[-7:]
    return Progress(
        today=week[-1],
        week=week,
        streak=streak_from(store.stats_active_days(), now),
        by_game=store.stats_by_game(week[0].day.isoformat()),
        totals=store.stats_totals(),
        goal=goal,
        activity=act,
        records=records(store),
        hours=hours(store),
        pos=pos_breakdown(store),
        words_read=store.words_read(),
    )


def minutes(seconds: int) -> str:
    m = seconds // 60
    if m < 60:
        return f"{m} мин"
    return f"{m // 60} ч {m % 60:02d} мин"
