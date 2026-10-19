"""Фоновый поток со своим asyncio-циклом.

Захват, OCR (WinRT) и сеть живут здесь, а не в потоке Qt:
* интерфейс не подвисает, пока Haiku думает;
* WinRT инициализирует апартамент в этом потоке как MTA и не спорит с Qt, который
  делает главный поток STA (OleInitialize).
Результаты возвращаются в Qt через сигналы (queued connection сам переносит поток).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

log = logging.getLogger(__name__)
T = TypeVar("T")


class BackgroundLoop:
    def __init__(self, name: str = "lexoplay-bg") -> None:
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()
        self._ready.wait()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        return self._loop

    def submit(self, coro: Coroutine[Any, Any, T]) -> concurrent.futures.Future[T]:
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)

        def _log(f: concurrent.futures.Future[T]) -> None:
            if not f.cancelled() and f.exception() is not None:
                log.error("фоновая задача упала", exc_info=f.exception())

        fut.add_done_callback(_log)
        return fut

    def run(self, coro: Coroutine[Any, Any, T], timeout: float | None = None) -> T:
        """Блокирующий вызов: только для самопроверки и тестов, не из потока Qt в игре."""
        return self.submit(coro).result(timeout)

    def stop(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=2)
