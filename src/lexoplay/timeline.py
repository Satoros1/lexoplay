"""История по сессиям: что и когда ты читал в каждой игре.

Сессии отдельно не хранятся, они собираются из событий: когда видели реплику и когда
сохранили слово. Полчаса тишины в игре = новая сессия (как session.IDLE_END_S). Так
сессии есть и у истории, накопленной до того, как они появились.

Две игры вперемешку (переключался между ними) дают две сессии, а не кашу из маленьких.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import date, datetime, timedelta

GAP = 30 * 60
MONTHS_GEN = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]  # fmt: skip


@dataclass(slots=True)
class PlaySession:
    exe: str
    start: float
    end: float
    lines: int = 0
    words: int = 0
    matches: int = 0  # реплик под поиск или избранное

    @property
    def key(self) -> str:
        return f"{self.exe}@{int(self.start)}"

    @property
    def seconds(self) -> int:
        return int(self.end - self.start)

    @property
    def day(self) -> date:
        return datetime.fromtimestamp(self.start).date()

    def contains(self, ts: float) -> bool:
        return self.start <= ts <= self.end


def split_sessions(events: list[tuple[str, float, str]], gap: float = GAP) -> list[PlaySession]:
    """events: (exe, время, "line" | "word"). Сессии от новых к старым."""
    by_exe: dict[str, list[tuple[float, str]]] = {}
    for exe, ts, kind in events:
        by_exe.setdefault(exe.lower(), []).append((ts, kind))
    out: list[PlaySession] = []
    for exe, items in by_exe.items():
        items.sort()
        cur: PlaySession | None = None
        for ts, kind in items:
            if cur is None or ts - cur.end > gap:
                cur = PlaySession(exe, ts, ts)
                out.append(cur)
            cur.end = ts
            if kind == "word":
                cur.words += 1
            else:
                cur.lines += 1
    out.sort(key=lambda s: s.end, reverse=True)
    return out


def count_matches(sessions: list[PlaySession], hits: list[tuple[str, float]]) -> list[PlaySession]:
    """Разложить найденные реплики по сессиям. Возвращает сессии, где что-то нашлось."""
    by_exe: dict[str, list[PlaySession]] = {}
    for s in sessions:
        s.matches = 0
        by_exe.setdefault(s.exe, []).append(s)
    starts: dict[str, list[float]] = {}
    for exe, items in by_exe.items():
        items.sort(key=lambda s: s.start)
        starts[exe] = [s.start for s in items]
    for exe, ts in hits:
        items = by_exe.get(exe.lower())
        if not items:
            continue
        i = bisect.bisect_right(starts[exe.lower()], ts) - 1
        if i >= 0 and items[i].contains(ts):
            items[i].matches += 1
    return [s for s in sessions if s.matches]


def day_title(d: date, today: date) -> str:
    if d == today:
        return "Сегодня"
    if d == today - timedelta(days=1):
        return "Вчера"
    name = f"{d.day} {MONTHS_GEN[d.month - 1]}"
    return name if d.year == today.year else f"{name} {d.year}"


def time_range(s: PlaySession) -> str:
    a, b = datetime.fromtimestamp(s.start), datetime.fromtimestamp(s.end)
    if b - a < timedelta(minutes=1):
        return f"{a:%H:%M}"
    if a.date() != b.date():
        return f"{a:%H:%M}–{b:%H:%M} (до {b.day} {MONTHS_GEN[b.month - 1]})"
    return f"{a:%H:%M}–{b:%H:%M}"


def duration(seconds: int) -> str:
    m = round(seconds / 60)
    if m < 1:
        return ""
    if m < 60:
        return f"{m} мин"
    return f"{m // 60} ч {m % 60:02d} мин"


def plural(n: int, one: str, few: str, many: str) -> str:
    """1 реплика, 2 реплики, 5 реплик."""
    k = abs(n) % 100
    if 11 <= k <= 14:
        return many
    k %= 10
    if k == 1:
        return one
    if 2 <= k <= 4:
        return few
    return many


def count(n: int, one: str, few: str, many: str) -> str:
    return f"{n} {plural(n, one, few, many)}"
