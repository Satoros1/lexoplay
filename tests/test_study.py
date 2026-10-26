"""Учёба: подсказки к трудным местам реплики (их выбирает модель), путь места от значения к «?» и к «знаю»,
ответ модели, пайплайн, база и оверлей."""

import asyncio
import os

import pytest

from lexoplay import study
from lexoplay.dedupe import LineMemory
from lexoplay.geometry import Rect
from lexoplay.ocr_model import Line, OcrResult, Word
from lexoplay.pipeline import Pipeline, Target
from lexoplay.recall import RecallIndex
from lexoplay.store import Store, StudyWord
from lexoplay.study import MIN_SHOW_S, PASSES_TO_KNOWN, SEEN_TO_TEST, Hint, Shown, StudyBook
from lexoplay.translate import (
    TranslateRequest,
    TranslateResult,
    build_system,
    build_user,
    clean_translation,
    parse_hints,
    split_hints,
    split_reply,
)

LINE = "But the way things are right now, there's no way to persuade anyone. They don't have the m ind to listen."
WORDS = LINE.split()


# ---------------------------------------------------------------- места реплики
def test_model_parts_are_found_on_screen_even_with_ocr_errors():
    assert [WORDS[i] for i in study.locate("the way things are right now", WORDS)] == "the way things are right now,".split()
    # OCR разбил mind на «m ind»: выражение всё равно находится целиком
    assert [WORDS[i] for i in study.locate("don't have the mind to", WORDS)] == ["don't", "have", "the", "m", "ind", "to"]
    assert study.locate("something else entirely", WORDS) == ()


def test_item_key_uses_dictionary_form_for_words_and_lowercase_for_expressions():
    assert study.item_key("persuaded") == "persuade"
    assert study.item_key("Don't have the mind to") == "don't have the mind to"


def test_model_hints_skip_known_and_keep_order_and_room():
    book = StudyBook([StudyWord("persuade", "known"), StudyWord("no way to", "test", 4, 0, "никак не")])
    items = [("the way things are right now", "при нынешнем положении"), ("persuade", "убедить"), ("no way to", "никак не"), ("listen", "слушать")]
    got = study.model_hints(WORDS, items, state=book.get, room=2)
    assert [(h.text, h.kind) for h in got] == [("the way things are right now", "new"), ("no way to", "test")]
    assert all(h.model and h.words for h in got)


def test_plan_finds_learning_items_and_saved_words_and_tells_model_what_is_known():
    book = StudyBook([
        StudyWord("persuade", "test", 4, 0, "убедить"),
        StudyWord("no way to", "new", 2, 0, "никак не"),
        StudyWord("right now", "known", 4, 2, "сейчас"),
    ])  # fmt: skip
    saved = RecallIndex([], lexo={"listen": "слушать"})
    p = study.plan(WORDS, learning=book.index(), state=book.get, saved=saved)
    assert [(h.text, h.kind, h.meaning) for h in p.hints] == [
        ("no way to", "new", "никак не"),
        ("persuade", "test", "убедить"),
        ("listen", "saved", "слушать"),
    ]
    assert set(p.known) == {"no way to", "persuade", "right now", "listen"}
    assert p.room == 2  # двое уже учатся: модели места на два новых


def test_merge_keeps_learning_first_and_caps_total():
    have = [Hint((1,), "a", "a", "test", "x"), Hint((5,), "s", "s", "saved", "y")]
    new = [Hint((i,), f"m{i}", f"m{i}", "new", "z", True) for i in (2, 3, 4)]
    got = study.merge(have, new)
    assert len(got) == study.MAX_HINTS and "s" not in [h.key for h in got]  # сохранённое уступает подсказкам
    assert [h.words for h in got] == [(1,), (2,), (3,), (4,)]


# ---------------------------------------------------------------- путь места
def _shown(text: str, hints: list[Hint]) -> Shown:
    return Shown("g.exe", text, tuple(text.split()), (), 0.0, hints=hints)


def test_item_goes_from_meaning_to_mark_to_known():
    book = StudyBook()
    for i in range(SEEN_TO_TEST):
        kind = "test" if book.get("no way to") and book.get("no way to").state == "test" else "new"
        assert kind == "new"
        book.commit(_shown(f"line {i}", [Hint((0,), "no way to", "no way to", "new", "никак не", True)]), shown_s=3, now=1.0)
    assert book.get("no way to").state == "test"
    learned = 0
    for i in range(PASSES_TO_KNOWN):
        _c, n, counted, forgotten = book.commit(
            _shown(f"again {i}", [Hint((0,), "no way to", "no way to", "test", "никак не")]), shown_s=3, now=2.0
        )
        assert counted and not forgotten
        learned += n
    assert book.get("no way to").state == "known" and learned == 1
    assert "no way to" in [e.word for e in book.index().entries]  # знаешь: модель про него больше не спросят


def test_forgotten_mark_comes_back_and_is_reported_peek_gives_no_credit():
    book = StudyBook([StudyWord("persuade", "test", 4, 0, "убедить")])
    sh = _shown("one", [Hint((0,), "persuade", "persuade", "test", "убедить")])
    sh.peeked = True
    book.commit(sh, shown_s=5, now=1.0)
    assert book.get("persuade").passes == 0
    sh = _shown("two", [Hint((0,), "persuade", "persuade", "test", "убедить")])
    sh.revealed.add("persuade")
    _c, _n, _counted, forgotten = book.commit(sh, shown_s=0.2, now=2.0)  # навёл: считается и на быстрой реплике
    w = book.get("persuade")
    assert (w.state, w.seen) == ("new", SEEN_TO_TEST - study.BACK_SEEN) and [h.key for h in forgotten] == ["persuade"]


def test_quick_or_repeated_line_is_not_counted_saved_words_are_not_tracked():
    book = StudyBook()
    h = Hint((0,), "persuade", "persuade", "new", "убедить", True)
    assert not book.commit(_shown("x", [h]), shown_s=MIN_SHOW_S / 2, now=1.0)[2]
    assert book.get("persuade").seen == 0 and book.get("persuade").meaning == "убедить"
    assert book.commit(_shown("x", [h]), shown_s=3, now=2.0)[2]
    assert not book.commit(_shown("x", [h]), shown_s=3, now=3.0)[2]  # F2 по той же реплике
    book.commit(_shown("y", [Hint((0,), "eerie", "eerie", "saved", "жуткий")]), shown_s=3, now=4.0)
    assert book.get("eerie") is None


# ---------------------------------------------------------------- ответ модели
def test_hint_line_is_parsed_and_never_shown_as_translation():
    raw = (
        "## the way things are right now = при нынешнем положении дел; don't have the mind to = не настроены\n"
        "Но при нынешнем положении дел никого не убедить. Они не настроены слушать.\n@@ But the way things are..."
    )
    rest, line, done = split_hints(raw)
    assert done and rest.startswith("Но при")
    assert parse_hints(line) == [("the way things are right now", "при нынешнем положении дел"), ("don't have the mind to", "не настроены")]
    assert split_reply(raw)[0].startswith("Но при")
    assert parse_hints("##") == [] and parse_hints("## Makoto = -") == []
    assert clean_translation("## the way = при", partial=True) == ""
    assert clean_translation("#", partial=True) == ""
    assert clean_translation("##\nНо при", partial=True) == "Но при"
    assert split_hints("#1 в рейтинге")[0] == "#1 в рейтинге"


def test_study_request_asks_for_hints_and_close_translation_without_touching_the_cached_system_part():
    plain = TranslateRequest(LINE, "Kyoko", "DR")
    studying = TranslateRequest(LINE, "Kyoko", "DR", study=True, study_level="B1", study_known=("persuade",), study_room=2)
    assert "<study" not in build_user(plain)
    assert build_user(studying).endswith('<study level="B1" max="2">known: persuade</study>')
    assert build_system(plain) == build_system(studying)  # системная часть та же: кеш промпта не сбрасывается
    system = build_system(plain)
    assert "Prefer whole expressions" in system and "translate closely" in system
    assert "keep every clause, qualifier and detail" in system  # и обычный перевод без сокращений


# ---------------------------------------------------------------- пайплайн
class HintTranslator:
    def __init__(self) -> None:
        self.requests: list[TranslateRequest] = []

    async def translate(self, req):
        self.requests.append(req)
        items = [("the way things are right now", "при нынешнем положении дел")] if req.study else None
        if req.on_hints and items is not None:
            req.on_hints(items)
        return TranslateResult(source=req.text, translation=f"RU:{req.text}", hints=tuple(items) if items else None)


def _ocr(text: str) -> OcrResult:
    return OcrResult((Line(tuple(Word(w, Rect(0.1 + i * 0.04, 0.8, 0.035, 0.03)) for i, w in enumerate(text.split()))),))


class _Ocr:
    def __init__(self, res: OcrResult) -> None:
        self.res = res

    async def recognize(self, prepared):
        return self.res


async def _no_sleep(_):
    return None


async def _direct(fn):
    return fn()


def test_pipeline_passes_plan_and_hints():
    from PIL import Image

    tr = HintTranslator()
    p = Pipeline(_Ocr(_ocr(LINE)), tr, LineMemory(), sleep=_no_sleep, run_sync=_direct)
    seen: list[tuple] = []
    plan = study.StudyPlan(known=("persuade",), room=2)
    target = Target("G", Rect(0.05, 0.75, 0.9, 0.2), study=lambda ocr: plan, study_level="B1")

    def grab():
        return Image.new("RGB", (400, 300), (250, 250, 250))

    out = asyncio.run(p.run(grab, target, on_study=lambda text, ocr, pl, items: seen.append((pl, items))))
    assert out.kind == "translated" and out.study is plan
    req = tr.requests[0]
    assert req.study and req.study_known == ("persuade",) and req.study_room == 2
    assert out.hints == (("the way things are right now", "при нынешнем положении дел"),)
    assert seen[0] == (plan, None) and seen[1][1] == list(out.hints)  # сначала план, потом подсказки (до перевода)
    again = asyncio.run(p.run(grab, target, on_study=lambda *a: seen.append(a)))
    assert again.kind == "repeat" and again.study is plan and again.hints is None
    plain = asyncio.run(p.run(grab, Target("G", Rect(0.05, 0.75, 0.9, 0.2))))
    assert plain.study is None and len(tr.requests) == 1


# ---------------------------------------------------------------- база и прогресс
def test_store_keeps_items_hints_and_days(tmp_path):
    from datetime import date, timedelta

    from lexoplay.progress import progress

    s = Store(tmp_path / "db.sqlite")
    s.study_put([StudyWord("persuade", "test", 4, 0, "убедить", 1.0), StudyWord("no way to", "known", 4, 2, "никак", 2.0)])
    s.study_put([StudyWord("persuade", "known", 4, 2, "убедить", 3.0)])
    assert {w.key: w.state for w in s.study_words()} == {"persuade": "known", "no way to": "known"}
    assert s.study_hints_get("g.exe", "ru", "line") is None
    s.study_hints_set("g.exe", "ru", "line", [("the way things are", "при нынешнем положении")])
    s.study_hints_set("g.exe", "ru", "quiet", [])
    assert s.study_hints_get("G.EXE", "ru", "line") == [("the way things are", "при нынешнем положении")]
    assert s.study_hints_get("g.exe", "ru", "quiet") == []  # подсказывать нечего: тоже ответ
    today = date.today()
    s.study_day_add(today.isoformat(), lines=10, peeks=2, cards=1)
    s.study_day_add(today.isoformat(), learned=2)
    s.study_day_add((today - timedelta(days=8)).isoformat(), lines=40, peeks=20)
    pr = progress(s, 10)
    assert pr.study_today == (10, 2, 2, 1) and pr.study_known == 2
    assert pr.study_share == 80 and pr.study_prev_share == 50  # неделю назад без перевода было 50%


def test_old_study_days_table_gets_cards_column(tmp_path):
    import sqlite3

    path = tmp_path / "old.sqlite"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE study_days (day TEXT PRIMARY KEY, lines INTEGER NOT NULL DEFAULT 0, "
               "peeks INTEGER NOT NULL DEFAULT 0, learned INTEGER NOT NULL DEFAULT 0)")  # fmt: skip
    db.execute("INSERT INTO study_days VALUES ('2026-10-25', 5, 1, 0)")
    db.commit()
    db.close()
    assert Store(path).study_days("2026-10-01") == {"2026-10-25": (5, 1, 0, 0)}


# ---------------------------------------------------------------- оверлей
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_overlay_hides_translation_and_shows_hint_row(qapp):
    from lexoplay.ui.overlay import HintView, Overlay

    ov = Overlay()
    ov.fade = False
    ov.resize(1280, 800)
    ov.set_display_rect(Rect(0, 0, 1280, 800))
    region = Rect(0.08, 0.72, 0.84, 0.22)
    ov.show_text(region, "Но при нынешнем положении дел никого не убедить. Они просто не настроены слушать.")
    full = ov.strip_rect()
    ov.study_hide = True
    pill = ov.strip_rect()
    assert pill.w < full.w * 0.5  # без подсказок: только «Alt · перевод»
    words = (Rect(0.12, 0.78, 0.06, 0.04), Rect(0.19, 0.78, 0.05, 0.04))
    ov.show_hints([
        HintView(words, "the way things", "при нынешнем положении дел", "new"),
        HintView((Rect(0.3, 0.84, 0.08, 0.04),), "persuade", "убедить", "saved"),
    ])  # fmt: skip
    row = ov.strip_rect()
    assert row.h < full.h * 1.6 and row.bottom <= region.y * 800  # ряд подсказок над окном диалога, не на тексте
    # курсор на подчёркнутом слове или на его подсказке: эта подсказка; на «Alt · перевод»: перевод
    assert ov.hover(0.31, 0.86) == (1, False)
    box, _f, label, _s, _o, chips = ov._strip_geometry(ov._shown())
    c = chips.chips[1]
    assert c.translation == "?" and ov.hover(c.rect.center().x() / 1280, c.rect.center().y() / 800) == (1, False)
    assert ov.hover(label.center().x() / 1280, label.center().y() / 800) == (None, True)
    assert HintView(words, "x", "y", "test").value() == "?" and HintView(words, "x", "y", "test").value(True) == "y"
    ov.set_study_open(True)
    assert ov.strip_rect().h > full.h  # перевод и под ним все значения
    ov.grab()  # рисуется без ошибок
    ov.hide_strip()
    assert ov.hints == () and not ov.study_open
