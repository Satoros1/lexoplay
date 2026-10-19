"""Локальная SQLite (%APPDATA%\\LexoPlay\\local.db).

games: профили игр (область диалога, способ захвата). Игра определяется по имени exe
в нижнем регистре: у новелл заголовок окна меняется по ходу игры, а exe нет.
outbox: запросы в Lexo, которые нельзя потерять (слова, конец сессии).
vocab: словарные формы слов из карточек Lexo, для жёлтой подсветки в F3.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path

from lexoplay.geometry import Rect

_SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    exe TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    region_json TEXT,
    name_region_json TEXT,
    capture TEXT,
    binarize INTEGER NOT NULL DEFAULT 0,
    last_played_at REAL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_at REAL NOT NULL DEFAULT 0,
    error TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS vocab (word TEXT PRIMARY KEY);
"""


@dataclass(frozen=True, slots=True)
class Task:
    id: int
    kind: str  # card | end
    payload: dict
    attempts: int
    status: str = "pending"
    error: str | None = None


@dataclass(frozen=True, slots=True)
class Game:
    exe: str
    title: str
    region: Rect | None = None
    name_region: Rect | None = None
    capture: str | None = None  # "screen" | "window"; None = ещё не выбрано
    binarize: bool = False
    last_played_at: float | None = None

    def with_(self, **kw: object) -> Game:
        return replace(self, **kw)  # type: ignore[arg-type]


def _rect_to(r: Rect | None) -> str | None:
    return json.dumps(r.to_dict()) if r else None


def _rect_from(s: str | None) -> Rect | None:
    return Rect.from_dict(json.loads(s)) if s else None


class Store:
    def __init__(self, path: Path | str) -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def get_game(self, exe: str) -> Game | None:
        with self._lock:
            row = self._db.execute(
                "SELECT exe, title, region_json, name_region_json, capture, binarize, last_played_at "
                "FROM games WHERE exe = ?",
                (exe.lower(),),
            ).fetchone()
        if row is None:
            return None
        return Game(row[0], row[1], _rect_from(row[2]), _rect_from(row[3]), row[4], bool(row[5]), row[6])

    def save_game(self, game: Game) -> Game:
        game = game.with_(exe=game.exe.lower())
        with self._lock:
            self._db.execute(
                "INSERT INTO games (exe, title, region_json, name_region_json, capture, binarize, last_played_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(exe) DO UPDATE SET title=excluded.title, region_json=excluded.region_json, "
                "name_region_json=excluded.name_region_json, capture=excluded.capture, "
                "binarize=excluded.binarize, last_played_at=excluded.last_played_at",
                (
                    game.exe,
                    game.title,
                    _rect_to(game.region),
                    _rect_to(game.name_region),
                    game.capture,
                    int(game.binarize),
                    game.last_played_at,
                ),
            )
            self._db.commit()
        return game

    def touch(self, exe: str) -> None:
        with self._lock:
            self._db.execute("UPDATE games SET last_played_at = ? WHERE exe = ?", (time.time(), exe.lower()))
            self._db.commit()

    def list_games(self) -> list[Game]:
        with self._lock:
            rows = self._db.execute("SELECT exe FROM games ORDER BY last_played_at DESC NULLS LAST").fetchall()
        return [g for (exe,) in rows if (g := self.get_game(exe)) is not None]

    def delete_game(self, exe: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM games WHERE exe = ?", (exe.lower(),))
            self._db.commit()

    def known_exes(self) -> set[str]:
        with self._lock:
            return {r[0] for r in self._db.execute("SELECT exe FROM games").fetchall()}

    # ------------------------------------------------------------------ meta
    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str | None) -> None:
        with self._lock:
            if value is None:
                self._db.execute("DELETE FROM meta WHERE key = ?", (key,))
            else:
                self._db.execute(
                    "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, value),
                )
            self._db.commit()

    # ------------------------------------------------------------------ outbox
    def outbox_add(self, kind: str, payload: dict) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO outbox (kind, payload, created_at) VALUES (?, ?, ?)",
                (kind, json.dumps(payload, ensure_ascii=False), time.time()),
            )
            self._db.commit()
            return int(cur.lastrowid or 0)

    def outbox_due(self, now: float, limit: int = 20) -> list[Task]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, kind, payload, attempts FROM outbox WHERE status = 'pending' AND next_at <= ? "
                "ORDER BY id LIMIT ?",
                (now, limit),
            ).fetchall()
        return [Task(r[0], r[1], json.loads(r[2]), r[3]) for r in rows]

    def outbox_next_at(self) -> float | None:
        with self._lock:
            row = self._db.execute("SELECT MIN(next_at) FROM outbox WHERE status = 'pending'").fetchone()
        return row[0] if row and row[0] is not None else None

    def outbox_due_now(self) -> None:
        """Связь вернулась: всё, что ждёт повтора, можно слать сразу."""
        with self._lock:
            self._db.execute("UPDATE outbox SET next_at = 0 WHERE status = 'pending'")
            self._db.commit()

    def outbox_done(self, task_id: int) -> None:
        with self._lock:
            self._db.execute("DELETE FROM outbox WHERE id = ?", (task_id,))
            self._db.commit()

    def outbox_retry_later(self, task_id: int, next_at: float, error: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE outbox SET attempts = attempts + 1, next_at = ?, error = ? WHERE id = ?",
                (next_at, error[:300], task_id),
            )
            self._db.commit()

    def outbox_fail(self, task_id: int, error: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE outbox SET status = 'failed', attempts = attempts + 1, error = ? WHERE id = ?",
                (error[:300], task_id),
            )
            self._db.commit()

    def outbox_counts(self) -> tuple[int, int]:
        """(ждут отправки, не отправились совсем)."""
        with self._lock:
            rows = dict(self._db.execute("SELECT status, COUNT(*) FROM outbox GROUP BY status").fetchall())
        return int(rows.get("pending", 0)), int(rows.get("failed", 0))

    def outbox_failed(self) -> list[Task]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, kind, payload, attempts, status, error FROM outbox WHERE status = 'failed' ORDER BY id"
            ).fetchall()
        return [Task(r[0], r[1], json.loads(r[2]), r[3], r[4], r[5]) for r in rows]

    def outbox_retry_failed(self) -> None:
        with self._lock:
            self._db.execute("UPDATE outbox SET status = 'pending', next_at = 0 WHERE status = 'failed'")
            self._db.commit()

    def outbox_drop_failed(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM outbox WHERE status = 'failed'")
            self._db.commit()

    # ------------------------------------------------------------------ vocab
    def vocab_load(self) -> set[str]:
        with self._lock:
            return {r[0] for r in self._db.execute("SELECT word FROM vocab").fetchall()}

    def vocab_add(self, words: list[str] | set[str]) -> None:
        with self._lock:
            self._db.executemany("INSERT OR IGNORE INTO vocab (word) VALUES (?)", [(w,) for w in words])
            self._db.commit()

    def vocab_replace(self, words: list[str] | set[str]) -> None:
        with self._lock:
            self._db.execute("DELETE FROM vocab")
            self._db.executemany("INSERT OR IGNORE INTO vocab (word) VALUES (?)", [(w,) for w in words])
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()
