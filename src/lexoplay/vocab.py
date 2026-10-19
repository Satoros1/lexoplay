"""Слова, которые уже есть в Lexo: в режиме F3 они подчёркнуты жёлтым.

Набор словарных форм (lemma или text карточки, в нижнем регистре) лежит в SQLite и
обновляется с сервера: целиком при запуске, потом раз в 10 минут только изменения.
Слово, добавленное через «+ в Lexo», попадает сюда сразу, не дожидаясь сервера.
"""

from __future__ import annotations

import threading

from lexoplay.store import Store
from lexoplay.words import forms


class Vocab:
    def __init__(self, store: Store) -> None:
        self._store = store
        self._lock = threading.Lock()
        self._words: set[str] = store.vocab_load()

    def __len__(self) -> int:
        return len(self._words)

    def known(self, word: str) -> bool:
        with self._lock:
            words = self._words
            return any(f in words for f in forms(word))

    def add(self, *words: str) -> None:
        clean = {w.strip().lower() for w in words if w and w.strip()}
        if not clean:
            return
        with self._lock:
            self._words |= clean
        self._store.vocab_add(clean)

    def replace(self, words: list[str], server_time: str) -> None:
        clean = {w.strip().lower() for w in words if w and w.strip()}
        with self._lock:
            self._words = clean
        self._store.vocab_replace(clean)
        self._store.set_meta("vocab_since", server_time or None)

    def merge(self, words: list[str], server_time: str) -> None:
        self.add(*words)
        if server_time:
            self._store.set_meta("vocab_since", server_time)

    def clear(self) -> None:
        self.replace([], "")

    @property
    def since(self) -> str | None:
        return self._store.get_meta("vocab_since")
