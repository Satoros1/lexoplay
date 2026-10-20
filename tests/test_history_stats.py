"""История по сессиям и статистика: что собирается из базы, без окна."""

import sqlite3
import time
from datetime import date, datetime, timedelta

from lexoplay.progress import longest_streak, pos_breakdown, pos_group, progress, records
from lexoplay.store import SavedWord, Store, english_words, fold
from lexoplay.timeline import count, count_matches, day_title, duration, split_sessions, time_range

T0 = datetime(2026, 10, 18, 20, 0).timestamp()


def _line(store, exe, norm, source, tr, at, speaker=None):
    store.line_add(exe, "ru", norm, speaker, source, tr)
    with store._lock:  # noqa: SLF001  время реплики задаём сами
        store._db.execute("UPDATE lines SET created_at = ?, seen_at = ? WHERE norm = ?", (at, at, norm))  # noqa: SLF001
        store._db.commit()  # noqa: SLF001


def _word(store, exe, word, at, pos="noun"):
    store.word_add(SavedWord(exe, "Game", word, word, "перевод", None, pos, f"{word}!", None, created_at=at))


def test_search_ignores_case_for_cyrillic_and_yo(tmp_path):
    store = Store(tmp_path / "db")
    store.line_add("dr1_us.exe", "ru", "a", "Monokuma", "Welcome to DESPAIR!", "Добро пожаловать в Отчаяние! Ещё немного.")
    assert store.lines_list(query="despair")
    assert store.lines_list(query="отчаяние")  # LIKE в SQLite сам по себе кириллицу без регистра не умеет
    assert store.lines_list(query="еще")  # ё = е
    assert store.lines_list(query="monokuma")  # и по имени говорящего
    assert not store.lines_list(query="100%")  # % в запросе не шаблон
    assert fold("Ёлка") == "елка"


def test_old_database_gets_star_column(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE lines (id INTEGER PRIMARY KEY AUTOINCREMENT, exe TEXT NOT NULL, norm TEXT NOT NULL, speaker TEXT,"
        " source TEXT NOT NULL, translation TEXT NOT NULL, lang TEXT NOT NULL DEFAULT 'ru', created_at REAL NOT NULL,"
        " seen_at REAL NOT NULL, hits INTEGER NOT NULL DEFAULT 0)"
    )
    db.execute("INSERT INTO lines (exe, norm, source, translation, created_at, seen_at) VALUES ('a.exe', 'x', 'X', 'Х', 1, 1)")
    db.commit()
    db.close()
    store = Store(path)
    line = store.lines_list()[0]
    assert line.star == 0
    store.line_star(line.id, True)
    assert store.lines_list()[0].star == 1


def test_sessions_split_by_pauses_and_games(tmp_path):
    store = Store(tmp_path / "db")
    for i in range(5):  # вечер: 5 реплик за 20 минут
        _line(store, "dr1_us.exe", f"a{i}", f"Line {i}", f"Реплика {i}", T0 + i * 300, "Makoto")
    _word(store, "dr1_us.exe", "despair", T0 + 400)
    for i in range(3):  # после перерыва в 2 часа: новая сессия
        _line(store, "dr1_us.exe", f"b{i}", f"Later {i}", f"Позже {i}", T0 + 3 * 3600 + i * 60)
    _line(store, "other.exe", "c0", "Hello", "Привет", T0 + 600)  # другая игра в то же время: своя сессия
    sessions = split_sessions(store.history_events())
    assert [(s.exe, s.lines, s.words) for s in sessions] == [
        ("dr1_us.exe", 3, 0),  # от новых к старым (по концу сессии)
        ("dr1_us.exe", 5, 1),
        ("other.exe", 1, 0),
    ]
    evening = sessions[1]
    assert evening.seconds == 1200 and duration(evening.seconds) == "20 мин"
    lines, total = store.lines_range("dr1_us.exe", evening.start, evening.end, limit=2)
    assert total == 5 and [x.norm for x in lines] == ["a3", "a4"]  # последние, от старых к новым
    assert [w.word for w in store.words_range("dr1_us.exe", evening.start, evening.end)] == ["despair"]
    # поиск раскладывается по сессиям
    found = count_matches(sessions, store.lines_matching(None, "later"))
    assert [(s.exe, s.matches) for s in found] == [("dr1_us.exe", 3)]
    store.line_star(store.lines_list(query="Line 2")[0].id, True)
    starred = count_matches(sessions, store.lines_matching("dr1_us.exe", starred=True))
    assert len(starred) == 1 and starred[0] is evening
    only, n = store.lines_range("dr1_us.exe", evening.start, evening.end, starred=True)
    assert n == 1 and only[0].source == "Line 2"


def test_readable_dates_and_counts():
    today = date(2026, 10, 20)
    assert day_title(today, today) == "Сегодня"
    assert day_title(today - timedelta(days=1), today) == "Вчера"
    assert day_title(date(2026, 3, 8), today) == "8 марта"
    assert day_title(date(2025, 12, 31), today) == "31 декабря 2025"
    assert duration(30) == "" and duration(3 * 3600 + 5 * 60) == "3 ч 05 мин"
    assert count(1, "реплика", "реплики", "реплик") == "1 реплика"
    assert count(3, "реплика", "реплики", "реплик") == "3 реплики"
    assert count(11, "реплика", "реплики", "реплик") == "11 реплик"
    assert count(22, "слово", "слова", "слов") == "22 слова"
    s = split_sessions([("a.exe", T0, "line"), ("a.exe", T0 + 3900, "line")], gap=7200)[0]
    assert time_range(s) == "20:00–21:05"


def test_records_streaks_parts_of_speech_and_words_read(tmp_path):
    store = Store(tmp_path / "db")
    today = date.today()
    for back, lines, words in ((0, 10, 2), (1, 40, 1), (2, 5, 7), (5, 3, 0)):
        store.stats_add((today - timedelta(days=back)).isoformat(), "dr1_us.exe", lines=lines, words=words, seconds=600)
    assert longest_streak(store.stats_active_days(limit=1000)) == 3
    now = time.time()
    for i in range(4):
        _line(store, "dr1_us.exe", f"l{i}", "I don't know what you're talking about.", "Не понимаю.", now - 3600 + i * 60, "Kyoko")
    _line(store, "dr1_us.exe", "m", "Puhuhu!", "Пухуху!", now - 3000, "Monokuma")
    for i, (w, pos) in enumerate((("dread", "noun"), ("loathe", "verb"), ("eerie", "adj"), ("make amends", "verb"), ("by the way", ""))):
        _word(store, "dr1_us.exe", w, now - 3500 + i, pos)
    rec = records(store)
    assert rec.best_lines == (today - timedelta(days=1), 40)
    assert rec.best_words == (today - timedelta(days=2), 7)
    assert rec.best_streak == 3
    assert rec.biggest is not None and rec.biggest.lines == 5
    assert english_words("I don't know what you're talking about.") == 7
    pr = progress(store, goal=10)
    assert pr.words_read == 4 * 7 + 1
    assert pr.streak == 3 and pr.week[-1].lines == 10 and pr.today.words == 2
    assert sum(pr.hours) == 5 and len(pr.activity) >= 7
    assert [(k, n) for k, _name, n in pr.pos] == [("phrase", 2), ("noun", 1), ("verb", 1), ("adj", 1)]
    assert pos_group("Phrasal verb", False) == "phrase" and pos_group("proper noun", False) == "noun"
    assert pos_breakdown(Store(tmp_path / "empty")) == []
    top = store.speaker_counts("dr1_us.exe", 1)
    assert top == [("Kyoko", 4)]


def test_explains_lookups_and_speaker_share(tmp_path):
    from lexoplay.lexo import Sense
    from lexoplay.lookup import (
        Explanation,
        ExplainItem,
        explanation_from_json,
        explanation_json,
        lookup_key,
        sense_from_json,
        sense_json,
    )

    store = Store(tmp_path / "db")
    exp = Explanation((ExplainItem("jump to conclusions", "спешить с выводами", "идиома"),), "Спокойный совет.")
    store.explain_put("DR1_US.exe", "ru", "dont jump", explanation_json(exp))
    assert explanation_from_json(store.explain_get("dr1_us.exe", "ru", "dont jump")) == exp
    assert store.explained("dr1_us.exe", "ru", ["dont jump", "other"]) == {"dont jump"}
    assert store.explained("dr1_us.exe", "ru", []) == set()
    store.explain_delete("dr1_us.exe", "ru", "dont jump")
    assert store.explain_get("dr1_us.exe", "ru", "dont jump") is None
    assert explanation_from_json("не json") is None

    sense = Sense("despair", "noun", "dɪˈspɛr", ["отчаяние", "безысходность"], "Добро пожаловать в **отчаяние**!")
    key = lookup_key("claude-haiku-5-5", "ru", "en", "Despair ", "Welcome to despair!", "Добро пожаловать в отчаяние!")
    assert key == lookup_key("claude-haiku-5-5", "ru", "en", "despair", "welcome to DESPAIR", "Добро пожаловать в отчаяние!")
    assert key != lookup_key("claude-haiku-5-5", "ru", "en", "despair", "Welcome to despair!", None)
    store.lookup_put(key, sense_json(sense))
    back = sense_from_json(store.lookup_get(key), "despair")
    assert back is not None and back.senses == sense.senses and back.marked_translation == sense.marked_translation
    for i in range(5):  # старые значения вытесняются
        store.lookup_put(f"k{i}", sense_json(sense), keep=3)
    assert store.lookup_get(key) is None and store.lookup_get("k4") is not None

    for i in range(20):
        store.line_add("vn.exe", "ru", f"n{i}", "Mira" if i < 2 else None, f"Line {i}", f"Реплика {i}")
    assert store.speaker_share("vn.exe") == (2, 20)


def test_retranslate_from_history_keeps_line_in_its_session(tmp_path):
    store = Store(tmp_path / "db")
    _line(store, "dr1_us.exe", "old", "My view became warped.", "Мой... исказился,", T0)
    store.line_retranslated("dr1_us.exe", "ru", "old", "Всё поплыло.", 3, touch=False)
    line = store.lines_list()[0]
    assert line.translation == "Всё поплыло." and line.seen_at == T0
    store.line_retranslated("dr1_us.exe", "ru", "old", "Всё поплыло и исказилось.", 3)
    assert store.lines_list()[0].seen_at > T0  # из игры («Перевести иначе» в F3): реплика свежая
