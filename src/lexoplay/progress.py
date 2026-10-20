"""Прогресс: реплики, слова и время по дням, серия дней подряд, цель на день.

Считается локально (таблица stats): реплика засчитывается, когда переведена впервые в
сессии, слово, когда сохранено, время, пока игра на экране.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

from lexoplay.store import Store

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


@dataclass(frozen=True, slots=True)
class DayStat:
    day: date
    lines: int = 0
    words: int = 0
    seconds: int = 0

    @property
    def label(self) -> str:
        return WEEKDAYS[self.day.weekday()]


@dataclass(frozen=True, slots=True)
class Progress:
    today: DayStat
    week: list[DayStat]  # 7 дней, последний = сегодня
    streak: int  # дней подряд с чтением или словами (сегодня или вчера включительно)
    by_game: list[tuple[str, int, int, int]]  # за 7 дней: exe, реплик, слов, секунд
    totals: tuple[int, int, int]
    goal: int

    @property
    def goal_done(self) -> bool:
        return self.today.words >= self.goal > 0


def streak_from(active_days: list[str], today: date) -> int:
    days = {date.fromisoformat(d) for d in active_days}
    cur = today if today in days else today - timedelta(days=1)
    n = 0
    while cur in days:
        n += 1
        cur -= timedelta(days=1)
    return n


def progress(store: Store, goal: int, today: Callable[[], date] = date.today) -> Progress:
    now = today()
    start = now - timedelta(days=6)
    raw = store.stats_days(start.isoformat())
    week = []
    for i in range(7):
        d = start + timedelta(days=i)
        lines, words, seconds = raw.get(d.isoformat(), (0, 0, 0))
        week.append(DayStat(d, lines, words, seconds))
    return Progress(
        today=week[-1],
        week=week,
        streak=streak_from(store.stats_active_days(), now),
        by_game=store.stats_by_game(start.isoformat()),
        totals=store.stats_totals(),
        goal=goal,
    )


def minutes(seconds: int) -> str:
    m = seconds // 60
    if m < 60:
        return f"{m} мин"
    return f"{m // 60} ч {m % 60:02d} мин"
