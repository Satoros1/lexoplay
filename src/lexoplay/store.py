"""Локальная SQLite (%APPDATA%\\LexoPlay\\local.db).

games: профили игр (область диалога, способ захвата). Игра определяется по имени exe
в нижнем регистре: у новелл заголовок окна меняется по ходу игры, а exe нет.
outbox: запросы в Lexo, которые нельзя потерять (слова, конец сессии).
vocab: словарные формы слов из карточек Lexo, для жёлтой подсветки в F3.
lines: переведённые реплики по играм. Это и кеш (та же реплика второй раз переводится
бесплатно и мгновенно), и история во вкладке «История».
words: слова, сохранённые через F3. Без привязки к Lexo живут только здесь, после
привязки сами уходят в карточки.
glossary: свои переводы имён и терминов для игры, их видит модель.
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
CREATE TABLE IF NOT EXISTS lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exe TEXT NOT NULL,
    norm TEXT NOT NULL,
    speaker TEXT,
    source TEXT NOT NULL,
    translation TEXT NOT NULL,
    lang TEXT NOT NULL DEFAULT 'ru',
    created_at REAL NOT NULL,
    seen_at REAL NOT NULL,
    hits INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS lines_exe_norm ON lines (exe, lang, norm);
CREATE INDEX IF NOT EXISTS lines_seen ON lines (exe, seen_at);
CREATE TABLE IF NOT EXISTS words (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exe TEXT,
    game TEXT,
    selection TEXT NOT NULL,
    lemma TEXT,
    translation TEXT,
    ipa TEXT,
    pos TEXT,
    sentence TEXT,
    sentence_translation TEXT,
    created_at REAL NOT NULL,
    lexo INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS glossary (
    exe TEXT NOT NULL,
    term TEXT NOT NULL,
    translation TEXT NOT NULL,
    PRIMARY KEY (exe, term)
);
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
    # куда слова этой игры идут в Lexo: game = своя колода по названию игры,
    # current = текущая колода в Lexo, deck = конкретная колода deck_id
    deck_mode: str = "game"
    deck_id: int | None = None
    deck_title: str | None = None
    # как готовить картинку для OCR (preprocess.MODES); None = ещё не подобрано
    ocr_mode: str | None = None

    @property
    def deck_label(self) -> str:
        if self.deck_mode == "deck" and self.deck_title:
            return f"«{self.deck_title}»"
        if self.deck_mode == "current":
            return "текущая колода"
        return f"«{self.title}»"

    def with_(self, **kw: object) -> Game:
        return replace(self, **kw)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class StoredLine:
    id: int
    exe: str
    norm: str
    speaker: str | None
    source: str
    translation: str
    lang: str
    created_at: float
    seen_at: float
    hits: int


@dataclass(frozen=True, slots=True)
class SavedWord:
    exe: str
    game: str
    selection: str
    lemma: str | None = None
    translation: str | None = None
    ipa: str | None = None
    pos: str | None = None
    sentence: str | None = None
    sentence_translation: str | None = None
    created_at: float = 0.0
    lexo: bool = False  # ушло (или стоит в очереди) в карточки Lexo
    id: int = 0

    @property
    def word(self) -> str:
        return self.lemma or self.selection


def _rect_to(r: Rect | None) -> str | None:
    return json.dumps(r.to_dict()) if r else None


def _rect_from(s: str | None) -> Rect | None:
    return Rect.from_dict(json.loads(s)) if s else None


class Store:
    def __init__(self, path: Path | str) -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.executescript(_SCHEMA)
        cols = {r[1] for r in self._db.execute("PRAGMA table_info(games)").fetchall()}
        for col, ddl in (
            ("deck_mode", "TEXT NOT NULL DEFAULT 'game'"),
            ("deck_id", "INTEGER"),
            ("deck_title", "TEXT"),
            ("ocr_mode", "TEXT"),
        ):
            if col not in cols:  # база из 0.1–0.2
                self._db.execute(f"ALTER TABLE games ADD COLUMN {col} {ddl}")
        self._db.commit()

    _COLS = (
        "exe, title, region_json, name_region_json, capture, binarize, last_played_at, "
        "deck_mode, deck_id, deck_title, ocr_mode"
    )

    @staticmethod
    def _game(row) -> Game:  # type: ignore[no-untyped-def]
        return Game(
            row[0], row[1], _rect_from(row[2]), _rect_from(row[3]), row[4], bool(row[5]), row[6],
            row[7] or "game", row[8], row[9], row[10],
        )

    def get_game(self, exe: str) -> Game | None:
        with self._lock:
            row = self._db.execute(f"SELECT {self._COLS} FROM games WHERE exe = ?", (exe.lower(),)).fetchone()
        return None if row is None else self._game(row)

    def save_game(self, game: Game) -> Game:
        game = game.with_(exe=game.exe.lower())
        with self._lock:
            self._db.execute(
                f"INSERT INTO games ({self._COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(exe) DO UPDATE SET title=excluded.title, region_json=excluded.region_json, "
                "name_region_json=excluded.name_region_json, capture=excluded.capture, "
                "binarize=excluded.binarize, last_played_at=excluded.last_played_at, "
                "deck_mode=excluded.deck_mode, deck_id=excluded.deck_id, deck_title=excluded.deck_title, "
                "ocr_mode=excluded.ocr_mode",
                (
                    game.exe,
                    game.title,
                    _rect_to(game.region),
                    _rect_to(game.name_region),
                    game.capture,
                    int(game.binarize),
                    game.last_played_at,
                    game.deck_mode,
                    game.deck_id,
                    game.deck_title,
                    game.ocr_mode,
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
            self._db.execute("DELETE FROM glossary WHERE exe = ?", (exe.lower(),))
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

    # ------------------------------------------------------------------ lines: кеш и история
    _LINE_COLS = "id, exe, norm, speaker, source, translation, lang, created_at, seen_at, hits"

    def line_find(self, exe: str, lang: str, norm: str) -> StoredLine | None:
        with self._lock:
            row = self._db.execute(
                f"SELECT {self._LINE_COLS} FROM lines WHERE exe = ? AND lang = ? AND norm = ? ORDER BY id DESC LIMIT 1",
                (exe.lower(), lang, norm),
            ).fetchone()
        return StoredLine(*row) if row else None

    def line_recent(self, exe: str, lang: str, limit: int = 400) -> list[StoredLine]:
        with self._lock:
            rows = self._db.execute(
                f"SELECT {self._LINE_COLS} FROM lines WHERE exe = ? AND lang = ? ORDER BY seen_at DESC LIMIT ?",
                (exe.lower(), lang, limit),
            ).fetchall()
        return [StoredLine(*r) for r in rows]

    def line_add(self, exe: str, lang: str, norm: str, speaker: str | None, source: str, translation: str) -> int:
        now = time.time()
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO lines (exe, norm, speaker, source, translation, lang, created_at, seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (exe.lower(), norm, speaker, source, translation, lang, now, now),
            )
            self._db.commit()
            return int(cur.lastrowid or 0)

    def line_seen(self, line_id: int) -> None:
        with self._lock:
            self._db.execute("UPDATE lines SET seen_at = ?, hits = hits + 1 WHERE id = ?", (time.time(), line_id))
            self._db.commit()

    def lines_list(self, exe: str | None = None, query: str = "", limit: int = 100, offset: int = 0) -> list[StoredLine]:
        where, args = [], []
        if exe:
            where.append("exe = ?")
            args.append(exe.lower())
        if query:
            where.append("(source LIKE ? OR translation LIKE ? OR speaker LIKE ?)")
            q = f"%{query}%"
            args += [q, q, q]
        sql = f"SELECT {self._LINE_COLS} FROM lines"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY seen_at DESC LIMIT ? OFFSET ?"
        with self._lock:
            rows = self._db.execute(sql, (*args, limit, offset)).fetchall()
        return [StoredLine(*r) for r in rows]

    def lines_count(self, exe: str | None = None) -> int:
        with self._lock:
            if exe:
                row = self._db.execute("SELECT COUNT(*) FROM lines WHERE exe = ?", (exe.lower(),)).fetchone()
            else:
                row = self._db.execute("SELECT COUNT(*) FROM lines").fetchone()
        return int(row[0])

    def line_delete(self, line_id: int) -> None:
        with self._lock:
            self._db.execute("DELETE FROM lines WHERE id = ?", (line_id,))
            self._db.commit()

    def lines_clear(self, exe: str | None = None) -> None:
        with self._lock:
            if exe:
                self._db.execute("DELETE FROM lines WHERE exe = ?", (exe.lower(),))
            else:
                self._db.execute("DELETE FROM lines")
            self._db.commit()

    def line_speakers(self, exe: str, limit: int = 30) -> list[str]:
        """Имена говорящих, которые встречались в игре: подсказка для глоссария."""
        with self._lock:
            rows = self._db.execute(
                "SELECT speaker, COUNT(*) AS n FROM lines WHERE exe = ? AND speaker IS NOT NULL AND speaker != '' "
                "GROUP BY speaker ORDER BY n DESC LIMIT ?",
                (exe.lower(), limit),
            ).fetchall()
        return [r[0] for r in rows]

    # ------------------------------------------------------------------ words: сохранённые слова
    _WORD_COLS = (
        "id, exe, game, selection, lemma, translation, ipa, pos, sentence, sentence_translation, created_at, lexo"
    )

    def word_add(self, w: SavedWord) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO words (exe, game, selection, lemma, translation, ipa, pos, sentence, "
                "sentence_translation, created_at, lexo) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    w.exe,
                    w.game,
                    w.selection,
                    w.lemma,
                    w.translation,
                    w.ipa,
                    w.pos,
                    w.sentence,
                    w.sentence_translation,
                    w.created_at or time.time(),
                    int(w.lexo),
                ),
            )
            self._db.commit()
            return int(cur.lastrowid or 0)

    def words_list(self, *, only_local: bool = False, limit: int = 2000) -> list[SavedWord]:
        sql = f"SELECT {self._WORD_COLS} FROM words"
        if only_local:
            sql += " WHERE lexo = 0"
        sql += " ORDER BY id DESC LIMIT ?"
        with self._lock:
            rows = self._db.execute(sql, (limit,)).fetchall()
        return [SavedWord(*r[1:11], lexo=bool(r[11]), id=r[0]) for r in rows]

    def words_mark_lexo(self, ids: list[int]) -> None:
        with self._lock:
            self._db.executemany("UPDATE words SET lexo = 1 WHERE id = ?", [(i,) for i in ids])
            self._db.commit()

    def word_delete(self, word_id: int) -> None:
        with self._lock:
            self._db.execute("DELETE FROM words WHERE id = ?", (word_id,))
            self._db.commit()

    def words_count(self) -> tuple[int, int]:
        """(всего, ещё не в Lexo)."""
        with self._lock:
            total, local = self._db.execute("SELECT COUNT(*), COALESCE(SUM(lexo = 0), 0) FROM words").fetchone()
        return int(total), int(local)

    # ------------------------------------------------------------------ glossary
    def glossary(self, exe: str) -> dict[str, str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT term, translation FROM glossary WHERE exe = ? ORDER BY term COLLATE NOCASE", (exe.lower(),)
            ).fetchall()
        return {r[0]: r[1] for r in rows}

    def glossary_set(self, exe: str, term: str, translation: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO glossary (exe, term, translation) VALUES (?, ?, ?) "
                "ON CONFLICT(exe, term) DO UPDATE SET translation = excluded.translation",
                (exe.lower(), term, translation),
            )
            self._db.commit()

    def glossary_delete(self, exe: str, term: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM glossary WHERE exe = ? AND term = ?", (exe.lower(), term))
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()
