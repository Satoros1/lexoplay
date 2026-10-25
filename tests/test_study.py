"""Учёба: какие слова получают подсказку, как слово переходит от значения к «?» и к «знаю»,
значения в ответе модели, пайплайн, база и оверлей."""

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
from lexoplay.study import MIN_SHOW_S, PASSES_TO_KNOWN, SEEN_TO_TEST, Shown, StudyBook
from lexoplay.translate import (
    TranslateRequest,
    TranslateResult,
    build_system,
    build_user,
    clean_translation,
    parse_glosses,
    split_glosses,
    split_reply,
)

NAMES = {"Makoto", "Kyoko", "Kirigiri"}


def picks(line: str, **kw):
    words = line.split()
    return [(words[p.index], p.key, p.kind) for p in study.pick(words, names=kw.pop("names", NAMES), **kw)]


# ---------------------------------------------------------------- выбор слов
def test_hard_words_only_not_names_or_common():
    got = picks("Makoto, the culprit tampered with the evidence. It was obviously a mistake.")
    assert [k for _w, k, _kind in got] == ["culprit", "tampered"]  # evidence, obviously, mistake: B1 их знает


def test_level_changes_what_is_hard():
    line = "The detective examined the corpse meticulously."
    b1 = {k for _w, k, _ in picks(line, level="b1")}
    b2 = {k for _w, k, _ in picks(line, level="b2")}
    assert {"corpse", "meticulously"} <= b1
    assert b2 <= b1 and "examine" not in b2


def test_at_most_three_new_words_rarest_first():
    line = "Culprit corpse meticulously tampered blabbermouth scrutinize."
    got = study.pick(line.split())
    assert len([p for p in got if p.kind == "new"]) == study.MAX_NEW


def test_known_and_skipped_words_get_nothing_test_words_get_a_mark():
    book = StudyBook([StudyWord("culprit", "known"), StudyWord("corpse", "test", 4, 0, "труп"), StudyWord("tampered", "skip")])
    got = picks("The culprit tampered with the corpse.", state=book.get)
    assert got == [("corpse.", "corpse", "test")]


def test_names_from_the_game_and_capitalized_unknown_words_are_not_hard():
    assert picks("Monokuma's despair spreads, Kirigiri.") == [("despair", "despair", "new")]
    assert picks("Kirigiri examined it.", names=set()) == [("examined", "examine", "new")]  # посреди: имя, хоть его и нет в names


def test_caps_line_and_stutter():
    assert picks("WHAT?! THE CULPRIT IS HERE?!") == [("CULPRIT", "culprit", "new")]
    got = study.pick(["You're", "a", "b-b-blabbermouth!"])
    assert [(p.token, p.key) for p in got] == [("blabbermouth", "blabbermouth")]


def test_saved_words_and_expressions_are_marks_with_own_meaning():
    idx = RecallIndex([], lexo={"beat around the bush": "ходить вокруг да около", "eerie": "жуткий"})
    words = "Don't beat around the bush. It feels eerie and the culprit knows.".split()
    got = study.pick(words, saved=idx)
    saved = [(words[p.index], p.meaning, p.label) for p in got if p.kind == "saved"]
    assert saved == [("beat", "ходить вокруг да около", "beat around the bush"), ("eerie", "жуткий", "")]
    assert not any(p.kind == "new" and p.key in ("bush", "eerie") for p in got)  # своё слово не идёт ещё и как новое
    assert [p.key for p in got if p.ask] == ["culprit"]


def test_lemma_and_meanings_match_word_forms():
    assert study.lemma("despaired") == "despair"
    ps = study.pick("They despaired, the culprits.".split())
    found = study.match_meanings(ps, {"despair": "отчаялись", "culprits": "преступники"})
    assert sorted(found.values()) == ["отчаялись", "преступники"]


# ---------------------------------------------------------------- состояния слова
def _shown(line: str, book: StudyBook, meanings: dict[str, str] | None = None) -> Shown:
    ps = tuple(study.pick(line.split(), state=book.get))
    sh = Shown("g.exe", line, ps, 0.0)
    for p in ps:
        m = (meanings or {}).get(p.key) or book.meaning(p.key) or f"значение {p.key}"
        sh.meanings[p.index] = m
    return sh


def test_word_goes_from_meaning_to_mark_to_known():
    book = StudyBook()
    for i in range(SEEN_TO_TEST):
        sh = _shown(f"The culprit is here {i}.", book)
        assert [p.kind for p in sh.picks] == ["new"]
        book.commit(sh, shown_s=MIN_SHOW_S + 1, now=1.0)
    assert book.get("culprit").state == "test"
    learned = 0
    for i in range(PASSES_TO_KNOWN):
        sh = _shown(f"The culprit again {i}.", book)
        assert [p.kind for p in sh.picks] == ["test"]
        _changed, n, counted = book.commit(sh, shown_s=MIN_SHOW_S + 1, now=2.0)
        assert counted
        learned += n
    assert book.get("culprit").state == "known" and learned == 1
    assert study.pick("The culprit returns.".split(), state=book.get) == []


def test_revealed_mark_brings_the_meaning_back_and_peek_gives_no_credit():
    book = StudyBook([StudyWord("culprit", "test", 4, 0, "преступник")])
    sh = _shown("The culprit is near.", book)
    sh.peeked = True
    book.commit(sh, shown_s=5, now=1.0)
    assert book.get("culprit").passes == 0  # подглядывал перевод: не засчитываем
    sh = _shown("The culprit is far.", book)
    sh.revealed.add(sh.picks[0].index)
    book.commit(sh, shown_s=0.2, now=2.0)  # навёл на «?»: засчитывается даже на быстрой реплике
    w = book.get("culprit")
    assert (w.state, w.seen) == ("new", SEEN_TO_TEST - study.BACK_SEEN)


def test_skipped_quickly_or_same_line_again_is_not_counted():
    book = StudyBook()
    sh = _shown("The culprit is here.", book)
    _c, _n, counted = book.commit(sh, shown_s=0.5, now=1.0)
    assert not counted and book.get("culprit").seen == 0 and book.get("culprit").meaning
    assert book.commit(_shown("The culprit is here.", book), shown_s=3, now=2.0)[2]
    assert not book.commit(_shown("The culprit is here.", book), shown_s=3, now=3.0)[2]  # F2 по той же реплике
    assert book.get("culprit").seen == 1


def test_model_skip_marks_word_as_not_worth_learning():
    book = StudyBook()
    sh = _shown("The detective nodded.", book, {"detective": study.SKIP})
    book.commit(sh, shown_s=3, now=1.0)
    assert book.get("detective").state == "skip"
    assert study.pick("The detective left.".split(), state=book.get) == []


# ---------------------------------------------------------------- ответ модели
def test_gloss_line_is_parsed_and_never_shown_as_translation():
    raw = "## culprit = преступник; Makoto = -; tampered = подделал улики\nПреступник подделал улики.\n@@ The culprit tampered."
    rest, line, done = split_glosses(raw)
    assert done and line.startswith("##") and rest.startswith("Преступник")
    assert parse_glosses(line) == {"culprit": "преступник", "makoto": "-", "tampered": "подделал улики"}
    assert split_reply(raw) == ("Преступник подделал улики.\n", "The culprit tampered.")
    assert clean_translation("## culprit = прест", partial=True) == ""
    assert clean_translation("#", partial=True) == ""
    assert clean_translation("## culprit = преступник\nПресту", partial=True) == "Престу"


def test_gloss_meanings_are_short_and_without_em_dash():
    got = parse_glosses("## eerie: жуткий — зловещий; scrutinize = внимательно изучать каждую мелочь в этом деле")
    assert got["eerie"] == "жуткий, зловещий"
    assert got["scrutinize"].endswith("…") and len(got["scrutinize"]) <= 30


def test_request_asks_for_glosses_only_when_studying():
    plain = TranslateRequest("The culprit is here.", None, "DR")
    asked = TranslateRequest("The culprit is here.", None, "DR", gloss_words=("culprit",), gloss_level="B1")
    assert "<gloss_words" not in build_user(plain)
    assert build_user(asked).endswith('<gloss_words level="B1">culprit</gloss_words>')
    assert build_system(plain) == build_system(asked)  # системная часть та же: кеш промпта не сбрасывается
    assert "<gloss_words>" in build_system(plain)


# ---------------------------------------------------------------- пайплайн
class GlossTranslator:
    def __init__(self) -> None:
        self.requests: list[TranslateRequest] = []

    async def translate(self, req):
        self.requests.append(req)
        found = {w.lower(): f"RU-{w.lower()}" for w in req.gloss_words}
        if req.on_glosses:
            req.on_glosses(found)
        return TranslateResult(source=req.text, translation=f"RU:{req.text}", glosses=found)


def _ocr(text: str) -> OcrResult:
    return OcrResult((Line(tuple(Word(w, Rect(0.1 + i * 0.05, 0.8, 0.04, 0.03)) for i, w in enumerate(text.split()))),))


class _Ocr:
    def __init__(self, res: OcrResult) -> None:
        self.res = res

    async def recognize(self, prepared):
        return self.res


async def _no_sleep(_):
    return None


async def _direct(fn):
    return fn()


def test_pipeline_picks_words_and_passes_glosses():
    from PIL import Image

    tr = GlossTranslator()
    p = Pipeline(_Ocr(_ocr("The culprit tampered with it.")), tr, LineMemory(), sleep=_no_sleep, run_sync=_direct)
    seen: list[tuple] = []
    target = Target(
        "G", Rect(0.05, 0.75, 0.9, 0.2), study=lambda ocr: study.pick([w.text for w in ocr.words]), study_level="B1"
    )

    def grab():
        return Image.new("RGB", (400, 300), (250, 250, 250))

    out = asyncio.run(p.run(grab, target, on_study=lambda text, ocr, ps, found: seen.append((text, len(ps), found))))
    assert out.kind == "translated"
    assert [x.key for x in out.picks] == ["culprit", "tampered"]
    assert tr.requests[0].gloss_words == ("culprit", "tampered")
    assert out.glosses == {"culprit": "RU-culprit", "tampered": "RU-tampered"}
    assert seen[0][2] is None and seen[1][2] == out.glosses  # сначала слова, потом значения (до перевода)
    again = asyncio.run(p.run(grab, target, on_study=lambda *a: seen.append(a)))
    assert again.kind == "repeat" and [x.key for x in again.picks] == ["culprit", "tampered"] and not again.glosses
    plain = asyncio.run(p.run(grab, Target("G", Rect(0.05, 0.75, 0.9, 0.2))))
    assert plain.picks == () and len(tr.requests) == 1


# ---------------------------------------------------------------- база и прогресс
def test_store_keeps_words_and_days(tmp_path):
    from datetime import date

    from lexoplay.progress import progress

    s = Store(tmp_path / "db.sqlite")
    s.study_put([StudyWord("culprit", "test", 4, 0, "преступник", 1.0), StudyWord("corpse", "known", 4, 2, "труп", 2.0)])
    s.study_put([StudyWord("culprit", "known", 4, 2, "преступник", 3.0)])
    assert {w.key: w.state for w in s.study_words()} == {"culprit": "known", "corpse": "known"}
    today = date.today().isoformat()
    s.study_day_add(today, lines=1, peeks=1)
    s.study_day_add(today, lines=1, learned=2)
    assert s.study_days(today) == {today: (2, 1, 2)}
    pr = progress(s, 10)
    assert pr.study_today == (2, 1, 2) and pr.study_week == (2, 1, 2) and pr.study_known == 2


# ---------------------------------------------------------------- оверлей
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_overlay_hides_translation_and_shows_glosses(qapp):
    from lexoplay.ui.overlay import GlossView, Overlay

    ov = Overlay()
    ov.fade = False
    ov.resize(1280, 800)
    ov.set_display_rect(Rect(0, 0, 1280, 800))
    region = Rect(0.08, 0.72, 0.84, 0.22)
    ov.show_text(region, "Преступник подделал улики, это очевидно и понятно всем, кто тут был в ту ночь.")
    full = ov.strip_rect()
    ov.study_hide = True
    pill = ov.strip_rect()
    assert pill.w < full.w * 0.5 and pill.h < full.h  # вместо перевода маленькая «Alt · перевод»
    ov.set_study_open(True)
    assert ov.strip_rect() == full
    ov.set_study_open(False)
    cx, cy = (pill.x + pill.w / 2) / 1280, (pill.y + pill.h / 2) / 800
    assert ov.strip_hit(cx, cy) and not ov.strip_hit(0.5, 0.1)
    word_a, word_b = Rect(0.12, 0.78, 0.06, 0.04), Rect(0.185, 0.78, 0.05, 0.04)
    ov.show_glosses([GlossView(word_a, "преступник", "new"), GlossView(word_b, "подделал", "test")])
    boxes = ov.gloss_boxes()
    assert len(boxes) == 2
    (_i, a, wa, _f), (_j, b, _wb, _g) = boxes
    assert a.bottom() <= wa.top() + 1 and b.left() >= a.right()  # над словом и не налезают друг на друга
    assert ov.gloss_at(0.15, 0.8) == 0
    assert GlossView(word_b, "подделал", "test").shown == "?"
    assert GlossView(word_b, "ходить вокруг да около", "saved", "beat around the bush", open=True).shown.startswith("beat around")
    ov.grab()  # рисуется без ошибок
    ov.hide_strip()
    assert ov.glosses == () and not ov.study_open
