"""«Минута после игры» (recap) и встречи слов: вопросы из реплик сессии, окно, редкие полезные слова."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from lexoplay import recap
from lexoplay.store import Met, Store

LINES = {
    "linger": ("She lingered at the door for a moment.", "Она задержалась у двери.", "lingered", "задерживаться"),
    "reluctant": ("He was reluctant to talk about it.", "Он не хотел об этом говорить.", "reluctant", "неохотный"),
    "end up": ("We always end up here somehow.", "Мы почему-то всегда оказываемся здесь.", "end up", "оказаться"),
    "fidget": ("The kids fidgeted during the speech.", "Дети ёрзали во время речи.", "fidgeted", "ёрзать"),
    "solemn": ("They made a solemn promise.", "Они дали торжественное обещание.", "solemn", "торжественный"),
    "gaze": ("Her gaze was calm.", "Её взгляд был спокоен.", "gaze", "взгляд"),
}


def met(key: str, result: str, kind: str = "new", at: float = 1.0) -> Met:
    sentence, tr, word, meaning = LINES[key]
    return Met("ks.exe", "s1", key, word, meaning, kind, result, sentence, tr, at)


def test_build_asks_forgotten_first_and_never_what_was_recalled() -> None:
    rows = [
        met("linger", "shown", at=1), met("reluctant", "recalled", "saved", at=2), met("end up", "forgot", "test", at=3),
        met("fidget", "shown", at=4), met("solemn", "peeked", "saved", at=5), met("gaze", "shown", at=6),
    ]  # fmt: skip
    qs = recap.build(rows, rng=random.Random(3))
    assert [q.key for q in qs] == ["end up", "gaze", "fidget", "linger", "solemn"]  # забытое, новые (свежие первыми), подглядел
    q = next(q for q in qs if q.key == "fidget")
    assert q.blanked == "The kids _____ during the speech." and q.answer == "fidget"  # вариант в словарной форме
    assert q.sentence[q.start : q.end] == "fidgeted" and len(q.options) == 3 and q.answer in q.options
    assert all(len(set(x.options)) == 3 for x in qs)
    # слово той же реплики в неверные варианты не идёт
    assert all(not recap._in_sentence(o, x.sentence) for x in qs for o in x.options if o != x.answer)
    # мало слов: окно не показываем вовсе
    assert recap.build(rows[:2], rng=random.Random(1)) == []
    assert recap.build([met("linger", "recalled")] * 5) == []


def test_build_uses_spare_words_and_skips_what_ocr_misread() -> None:
    rows = [met("linger", "forgot"), met("fidget", "shown"), met("gaze", "shown")]
    broken = Met("ks.exe", "s1", "solemn", "solemn", "торжественный", "new", "shown", "They made a so1emn promise.", "", 9.0)
    qs = recap.build([*rows, broken], extra=["eerie", "shrug"], rng=random.Random(2))
    assert sorted(q.key for q in qs) == ["fidget", "gaze", "linger"]


def test_rare_useful_words() -> None:
    rows = [met("linger", "shown"), met("solemn", "shown"), met("gaze", "shown"), met("end up", "shown"), met("fidget", "forgot", "test")]
    totals = {"linger": 1, "solemn": 2, "gaze": 40, "end up": 1, "fidget": 1}
    ranks = {"linger": 9000, "solemn": 7000, "gaze": 5000, "fidget": None}
    got = recap.rare_words(rows, total_in_novel=lambda w: totals.get(w, 0), rank=ranks.get, known=lambda w: False)
    assert [r.key for r in got] == ["solemn", "linger"]  # gaze игра покажет ещё 40 раз, end up выражение
    assert recap.rare_words(rows, total_in_novel=lambda w: 1, rank=lambda w: 100, known=lambda w: True) == []


def test_met_rows_roundtrip_and_prune(tmp_path: Path) -> None:
    store = Store(tmp_path / "local.db")
    store.met_add([met("linger", "shown", at=100.0), met("gaze", "forgot", "saved", at=200.0)])
    rows = store.met_session("s1")
    assert [(r.key, r.result, r.kind) for r in rows] == [("linger", "shown", "new"), ("gaze", "forgot", "saved")]
    assert rows[0].sentence.startswith("She lingered") and store.met_session("other") == []
    store.met_prune(150.0)
    assert [r.key for r in store.met_session("s1")] == ["gaze"]


@pytest.fixture(scope="module")
def qapp():  # type: ignore[no-untyped-def]
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_recap_dialog_one_click_per_word(qapp) -> None:  # type: ignore[no-untyped-def]
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from lexoplay.ui.recap_dialog import RecapDialog

    rows = [met("linger", "forgot", "saved"), met("fidget", "shown"), met("gaze", "shown")]
    qs = recap.build(rows, extra=["eerie", "shrug"], rng=random.Random(5))
    rare = [recap.Rare("solemn", "solemn", "торжественный", "They made a solemn promise.", "")]
    answers: list[tuple[str, bool]] = []
    saved: list[list[str]] = []

    class Ctl:
        def recap_answer(self, exe, title, q, correct) -> None:  # type: ignore[no-untyped-def]
            answers.append((q.key, correct))

        def recap_save(self, exe, title, words) -> None:  # type: ignore[no-untyped-def]
            saved.append([w.key for w in words])

    dlg = RecapDialog(Ctl(), "ks.exe", "Katawa Shoujo", qs, rare)
    dlg.show()
    assert dlg.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)  # фокус не забирает
    assert dlg.progress.text() == "1 из 3" and "_____" in dlg.sentence.text() and dlg.next.isHidden()
    q = qs[0]
    QTest.keyClick(dlg, Qt.Key.Key_1 + q.options.index(q.answer))  # правильный вариант цифрой
    assert answers == [(q.key, True)] and not dlg.next.isHidden() and q.meaning in dlg.meaning.text()
    assert "_____" not in dlg.sentence.text()
    QTest.keyClick(dlg, Qt.Key.Key_2)  # после ответа цифры уже ничего не меняют
    assert len(answers) == 1
    QTest.keyClick(dlg, Qt.Key.Key_Return)
    q = qs[1]
    wrong = next(i for i, o in enumerate(q.options) if o != q.answer)
    dlg.options[wrong].click()
    assert answers[-1] == (q.key, False)
    dlg.next.click()
    q = qs[2]
    dlg.options[q.options.index(q.answer)].click()
    dlg.next.click()
    # итог и полезные слова, которых в игре больше не будет
    assert "2 из 3" in dlg.sentence.text() and dlg.rare_list.count() == 1 and not dlg.save.isHidden()
    assert "—" not in dlg.rare_note.text() and "ещё пару раз" in dlg.rare_note.text()
    dlg.save.click()
    assert saved == [["solemn"]] and not dlg.isVisible()
    import shiboken6

    shiboken6.delete(dlg)
