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
        self._tr: dict[str, str] = store.vocab_translations()
        self.version = 0  # меняется с каждым обновлением: по нему пересобирается подсказка под переводом

    def translations(self) -> dict[str, str]:
        """Слово -> перевод (с сервера Lexo). Для подсказки «уже сохранял» под переводом реплики."""
        with self._lock:
            return dict(self._tr)

    def __len__(self) -> int:
        return len(self._words)

    def known(self, word: str) -> bool:
        with self._lock:
            words = self._words
            return any(f in words for f in forms(word))

    def add(self, *words: str, translations: dict[str, str] | None = None) -> None:
        clean = {w.strip().lower() for w in words if w and w.strip()}
        if not clean:
            return
        tr = {k: v for k, v in (translations or {}).items() if k in clean and v}
        with self._lock:
            self._words |= clean
            self._tr.update(tr)
            self.version += 1
        self._store.vocab_add(clean, tr)

    def replace(self, words: list[str], server_time: str, translations: dict[str, str] | None = None) -> None:
        clean = {w.strip().lower() for w in words if w and w.strip()}
        tr = {k: v for k, v in (translations or {}).items() if k in clean and v}
        with self._lock:
            self._words = clean
            self._tr = tr
            self.version += 1
        self._store.vocab_replace(clean, tr)
        self._store.set_meta("vocab_since", server_time or None)

    def merge(self, words: list[str], server_time: str, translations: dict[str, str] | None = None) -> None:
        self.add(*words, translations=translations)
        if server_time:
            self._store.set_meta("vocab_since", server_time)

    def clear(self) -> None:
        self.replace([], "")

    @property
    def since(self) -> str | None:
        return self._store.get_meta("vocab_since")
