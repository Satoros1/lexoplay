"""Очередь слов и концов сессий: ничего не теряется, не дублируется и не долбит сервер."""

import asyncio

import pytest

from lexoplay.lexo import LexoError, LexoOffline
from lexoplay.outbox import Outbox, backoff
from lexoplay.store import Store


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class Server:
    """Поддельный сервер: каждая задача получает ответ из сценария, по умолчанию успех."""

    def __init__(self) -> None:
        self.script: list[object] = []
        self.got: list[tuple[str, dict]] = []

    async def send(self, task):
        if self.script:
            outcome = self.script.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
        self.got.append((task.kind, task.payload))
        return {"text": task.payload.get("lemma", "")}


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / "local.db")
    server = Server()
    clock = Clock()
    events = {"changes": 0, "paused": [], "sent": []}
    box = Outbox(
        store,
        server.send,
        on_change=lambda: events.__setitem__("changes", events["changes"] + 1),
        on_paused=lambda e: events["paused"].append(e.code),
        on_sent=lambda t, r: events["sent"].append(r),
        clock=clock,
    )
    yield store, server, clock, box, events
    store.close()


def flush(box):
    return asyncio.run(box.flush())


def test_backoff_grows_and_caps():
    assert [backoff(a) for a in range(4)] == [2, 4, 8, 16]
    assert backoff(30) == 300


def test_sends_in_order_and_removes(setup):
    store, server, _clock, box, events = setup
    box.enqueue("card", {"lemma": "eerie"})
    box.enqueue("end", {"session_id": "s1"})
    assert box.counts() == (2, 0)
    assert flush(box) == 2
    assert [k for k, _ in server.got] == ["card", "end"]
    assert box.counts() == (0, 0)
    assert events["sent"][0] == {"text": "eerie"}


def test_offline_holds_everything_then_retries(setup):
    store, server, clock, box, _ = setup
    box.enqueue("card", {"lemma": "a"})
    box.enqueue("card", {"lemma": "b"})
    box.enqueue("card", {"lemma": "c"})
    server.script = [LexoOffline("down")]
    assert flush(box) == 0
    assert server.got == []  # после первой ошибки сети остальные даже не пробуем
    assert box.counts() == (3, 0)
    clock.t += 1
    assert flush(box) == 0  # пауза ещё не кончилась
    clock.t += 5
    assert flush(box) == 3
    assert [p["lemma"] for _, p in server.got] == ["a", "b", "c"]


def test_three_words_offline_become_exactly_three_cards(setup):
    """Сценарий из техспеки: выдернуть сеть, добавить 3 слова, вернуть сеть."""
    _store, server, clock, box, _ = setup
    server.script = [LexoOffline("x"), LexoOffline("x")]
    for w in ("eerie", "reluctant", "make amends"):
        box.enqueue("card", {"lemma": w})
        flush(box)
        clock.t += 1
    clock.t += 600
    flush(box)
    assert sorted(p["lemma"] for _, p in server.got) == ["eerie", "make amends", "reluctant"]
    assert box.counts() == (0, 0)


def test_nudge_ends_the_wait(setup):
    _store, server, _clock, box, _ = setup
    box.enqueue("card", {"lemma": "a"})
    server.script = [LexoOffline("x")]
    flush(box)
    assert flush(box) == 0
    box.nudge()  # сервер только что ответил на перевод: связь есть
    assert flush(box) == 1


def test_rejected_task_fails_instead_of_looping(setup):
    store, server, _clock, box, _ = setup
    box.enqueue("card", {"lemma": "bad"})
    box.enqueue("card", {"lemma": "good"})
    server.script = [LexoError(422, "http_error", "Неверный запрос")]
    assert flush(box) == 1
    assert box.counts() == (0, 1)
    failed = store.outbox_failed()
    assert failed[0].payload["lemma"] == "bad" and failed[0].error == "Неверный запрос"
    box.retry_failed()
    assert flush(box) == 1
    assert box.counts() == (0, 0)


def test_server_overload_is_retried(setup):
    _store, server, clock, box, _ = setup
    box.enqueue("card", {"lemma": "a"})
    server.script = [LexoError(503, "ai_unavailable", "Перевод недоступен."), LexoError(429, "too_many_requests", "")]
    flush(box)
    clock.t += 3
    flush(box)
    clock.t += 10
    assert flush(box) == 1


def test_revoked_device_pauses_until_relinked(setup):
    _store, server, clock, box, events = setup
    box.enqueue("card", {"lemma": "a"})
    server.script = [LexoError(401, "device_revoked", "Привязка снята.")]
    flush(box)
    assert box.paused and events["paused"] == ["device_revoked"]
    clock.t += 1000
    assert flush(box) == 0 and box.counts() == (1, 0)  # задача не потерялась
    box.resume()
    assert flush(box) == 1


def test_tasks_survive_restart(tmp_path):
    store = Store(tmp_path / "local.db")
    Outbox(store, None).enqueue("card", {"lemma": "kept"})  # type: ignore[arg-type]
    store.close()
    store2 = Store(tmp_path / "local.db")
    got = []

    async def send(task):
        got.append(task.payload["lemma"])

    assert asyncio.run(Outbox(store2, send).flush()) == 1
    assert got == ["kept"]
    store2.close()


def test_run_forever_wakes_on_enqueue(tmp_path):
    store = Store(tmp_path / "local.db")
    got = []

    async def send(task):
        got.append(task.payload["n"])

    box = Outbox(store, send)

    async def scenario():
        worker = asyncio.create_task(box.run_forever())
        await asyncio.sleep(0.05)
        box.enqueue("card", {"n": 1})
        for _ in range(50):
            if got:
                break
            await asyncio.sleep(0.02)
        worker.cancel()

    asyncio.run(scenario())
    assert got == [1]
    store.close()
