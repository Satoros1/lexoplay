"""Очередь запросов в Lexo, которые нельзя потерять: добавленные слова и конец сессии.

Игрок нажал «+ в Lexo» → задача сразу в SQLite, тост «сохранено» показываем сразу.
Один воркер в фоновом цикле отправляет задачи по порядку. Нет сети или сервер
перегружен → повтор через 2, 4, 8… секунд, но не реже раза в 5 минут. Отказ по
существу (4xx, кроме 429) → задача «не отправлена», видна в трее, а не крутится вечно.
Привязка снята (401) или клиент устарел (426) → очередь на паузе до новой привязки.
POST /desktop/cards идемпотентен по idempotency_key, так что повтор не создаст дубль.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from lexoplay.lexo import LexoError, LexoOffline
from lexoplay.store import Store, Task

log = logging.getLogger(__name__)

MAX_DELAY = 300.0

Sender = Callable[[Task], Awaitable[Any]]


def backoff(attempts: int) -> float:
    """attempts = сколько раз уже не получилось: 2, 4, 8 … 300."""
    return min(MAX_DELAY, 2.0 ** max(1, attempts + 1))


class Outbox:
    def __init__(
        self,
        store: Store,
        send: Sender,
        *,
        on_change: Callable[[], None] | None = None,
        on_paused: Callable[[LexoError], None] | None = None,
        on_sent: Callable[[Task, Any], None] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._send = send
        self._on_change = on_change or (lambda: None)
        self._on_paused = on_paused or (lambda _e: None)
        self._on_sent = on_sent or (lambda _t, _r: None)
        self._clock = clock
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._hold_until = 0.0
        self.paused = False

    # ------------------------------------------------------------------ из любого потока
    def enqueue(self, kind: str, payload: dict[str, Any]) -> int:
        task_id = self._store.outbox_add(kind, payload)
        self._on_change()
        self.wake()
        return task_id

    def wake(self) -> None:
        if self._loop is not None and self._wake is not None:
            self._loop.call_soon_threadsafe(self._wake.set)

    def resume(self) -> None:
        self.paused = False
        self._hold_until = 0.0
        self.wake()

    def nudge(self) -> None:
        """Связь точно есть (только что ответил сервер): не ждать конца паузы после ошибки сети."""
        if self._hold_until > self._clock():
            self._hold_until = 0.0
            self._store.outbox_due_now()
            self.wake()

    def counts(self) -> tuple[int, int]:
        return self._store.outbox_counts()

    def retry_failed(self) -> None:
        self._store.outbox_retry_failed()
        self._on_change()
        self.wake()

    # ------------------------------------------------------------------ воркер
    async def flush(self) -> int:
        """Отправить всё, что пора отправить. Возвращает число отправленных задач.

        Первая же ошибка сети или перегрузка сервера останавливает проход: остальные
        задачи подождут вместе с ней, а не будут по очереди биться в ту же стену.
        """
        sent = 0
        while not self.paused and self._clock() >= self._hold_until:
            due = self._store.outbox_due(self._clock())
            if not due:
                break
            for task in due:
                if self.paused or self._clock() < self._hold_until:
                    break
                sent += int(await self._process(task))
        return sent

    async def _process(self, task: Task) -> bool:
        try:
            result = await self._send(task)
        except LexoOffline as exc:
            self._later(task, f"нет связи: {exc}")
            return False
        except LexoError as exc:
            if exc.status in (401, 426):
                self.paused = True
                log.info("outbox на паузе: %s", exc.code)
                self._on_paused(exc)
                return False
            if exc.retryable:
                self._later(task, f"{exc.status} {exc.code}")
                return False
            log.warning("outbox: задача %s %s отклонена: %s %s", task.id, task.kind, exc.status, exc.message)
            self._store.outbox_fail(task.id, exc.message)
            self._on_change()
            return False
        except Exception as exc:  # наша ошибка: не терять задачу, но и не долбить сервер
            log.exception("outbox: задача %s упала", task.id)
            delay = backoff(task.attempts)
            self._store.outbox_retry_later(task.id, self._clock() + delay, repr(exc))
            return False
        self._store.outbox_done(task.id)
        self._on_sent(task, result)
        self._on_change()
        return True

    def _later(self, task: Task, why: str) -> None:
        delay = backoff(task.attempts)
        self._hold_until = self._clock() + delay
        self._store.outbox_retry_later(task.id, self._hold_until, why)
        log.info("outbox: задача %s повтор через %.0f с (%s)", task.id, delay, why)

    async def run_forever(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        while True:
            try:
                await self.flush()
            except Exception:
                log.exception("outbox: цикл упал, продолжаем")
            nxt = self._store.outbox_next_at()
            if nxt is None or self.paused:
                wait = 60.0
            else:
                wait = max(0.5, min(MAX_DELAY, max(nxt, self._hold_until) - self._clock()))
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=wait)
            except TimeoutError:
                pass
            self._wake.clear()
