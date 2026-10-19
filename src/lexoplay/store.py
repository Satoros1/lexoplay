"""Локальная SQLite: профили игр (область диалога, способ захвата).

Игра определяется по имени exe (в нижнем регистре): у новелл заголовок окна часто
меняется по ходу игры, а exe нет.
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
"""


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

    def close(self) -> None:
        with self._lock:
            self._db.close()
