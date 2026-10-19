"""Игровые сессии: один заход в одну игру. Нужны для итога в боте и истории в Mini App.

Отдельного запроса «старт» нет: клиент сам придумывает session_id и шлёт его в каждом
запросе, сервер создаёт сессию при первом упоминании. Конец, при первом из событий:
- окно игры закрыто (ждём 5 минут: перезапустил игру, значит продолжаем ту же сессию);
- 30 минут без F2/F3;
- выход из LexoPlay.
Тогда в outbox уходит end с длительностью и числом реплик.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

IDLE_END_S = 30 * 60
REOPEN_GRACE_S = 5 * 60


@dataclass
class GameSession:
    id: str
    exe: str
    title: str
    started_at: float
    last_action_at: float
    lines: int = 0
    words: int = 0
    closing_since: float | None = None  # окно игры закрыли, ждём возврата


EndSink = Callable[[GameSession, int, float], None]  # (сессия, длительность, ended_at)


class Sessions:
    def __init__(self, on_end: EndSink, clock: Callable[[], float] = time.time) -> None:
        self._on_end = on_end
        self._clock = clock
        self._by_exe: dict[str, GameSession] = {}

    def get(self, exe: str) -> GameSession | None:
        return self._by_exe.get(exe.lower())

    def touch(self, exe: str, title: str) -> GameSession:
        """F2 или F3 в игре: открыть сессию или продолжить текущую."""
        exe = exe.lower()
        now = self._clock()
        s = self._by_exe.get(exe)
        if s is not None and s.closing_since is None and now - s.last_action_at >= IDLE_END_S:
            self._finish(s, s.last_action_at)
            s = None
        if s is not None and s.closing_since is not None:
            if now - s.closing_since < REOPEN_GRACE_S:
                s.closing_since = None  # игру перезапустили быстро: та же сессия
            else:
                self._finish(s, s.closing_since)
                s = None
        if s is None:
            s = GameSession(str(uuid.uuid4()), exe, title, started_at=now, last_action_at=now)
            self._by_exe[exe] = s
        s.last_action_at = now
        s.title = title or s.title
        return s

    def line_translated(self, exe: str) -> None:
        if s := self.get(exe):
            s.lines += 1

    def word_added(self, exe: str) -> None:
        if s := self.get(exe):
            s.words += 1

    def game_closed(self, exe: str) -> None:
        s = self.get(exe)
        if s is not None and s.closing_since is None:
            s.closing_since = self._clock()

    def tick(self) -> None:
        """Раз в минуту: закрыть брошенные сессии."""
        now = self._clock()
        for s in list(self._by_exe.values()):
            if s.closing_since is not None and now - s.closing_since >= REOPEN_GRACE_S:
                self._finish(s, s.closing_since)
            elif s.closing_since is None and now - s.last_action_at >= IDLE_END_S:
                self._finish(s, s.last_action_at)

    def end_all(self) -> None:
        """Выход из программы."""
        now = self._clock()
        for s in list(self._by_exe.values()):
            self._finish(s, s.closing_since or now)

    def _finish(self, s: GameSession, ended_at: float) -> None:
        self._by_exe.pop(s.exe, None)
        duration = max(0, round(ended_at - s.started_at))
        self._on_end(s, duration, ended_at)
