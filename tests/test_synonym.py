"""Простой синоним к сложному слову: показываем, только если он знакомый и проще самого слова."""

import asyncio
import os

import httpx
import pytest

from lexoplay.lexo import GameRef, LexoClient
from lexoplay.lookup import build_prompt, parse_sense, sense_from_json, sense_json
from lexoplay.store import SavedWord, Store
from lexoplay.synonym import simple_hint
from lexoplay.wordmode import CardState


def test_simple_hint_keeps_only_known_and_simpler_words():
    assert simple_hint("argument against", "rebuttal") == "argument against"
    assert simple_hint("an answer that proves someone wrong", "rebuttal") == "an answer that proves someone wrong"
    assert simple_hint("≈ not wanting to.", "reluctant") == "not wanting to"
    assert simple_hint('"scary"', "eerie") == "scary"
    # сам синоним редкий: это ещё одно новое слово, а не опора
    assert simple_hint("counterargument", "rebuttal") == ""
    assert simple_hint("unwilling", "reluctant") == ""
    # то же слово или однокоренное ничего не объясняет
    assert simple_hint("to rebut", "rebuttal") == "" and simple_hint("rebuttal", "rebuttal") == ""
    # слово и так частое: синоним не нужен
    assert simple_hint("big", "large") == ""
    # не английский, слишком длинный, пустой
    assert simple_hint("ловушка", "lure") == ""
    assert simple_hint("a thing that you say when you want to show that someone is wrong", "rebuttal") == ""
    assert simple_hint("", "rebuttal") == "" and simple_hint(None, "rebuttal") == ""


def test_prompt_asks_for_simple_word_and_unambiguous_meanings():
    prompt = build_prompt("rebuttal", "Her rebuttal was perfect.", None, "ru", "en")
    assert '"simple"' in prompt and "A2 to B1" in prompt
    assert "возражение would be read as objection" in prompt  # значения без двусмысленности
    s = parse_sense(
        '{"lemma": "rebuttal", "pos": "noun", "ipa": "rɪˈbʌtl", "senses": ["опровержение", "контраргумент"], '
        '"simple": "argument against"}',
        "rebuttal",
    )
    assert s.simple == "argument against"
    assert sense_from_json(sense_json(s), "rebuttal") == s
    rare = parse_sense('{"lemma": "rebuttal", "senses": ["опровержение"], "simple": "counterargument"}', "rebuttal")
    assert rare.simple == ""
    old = sense_from_json('{"lemma": "go", "senses": ["идти"]}', "go")  # кеш старых версий без поля
    assert old is not None and old.simple == ""


def test_server_simple_is_checked_too():
    def handler(req):
        return httpx.Response(200, json={"lemma": "reluctant", "senses": ["неохотный"], "simple": "not wanting to"})

    client = LexoClient("https://lexo.test/", "tok", transport=httpx.MockTransport(handler))
    sense = asyncio.run(
        client.lookup(session_id="s" * 36, game=GameRef("G"), selection="reluctant", sentence="x", sentence_translation=None)
    )
    assert sense.simple == "not wanting to"

    def rare(req):
        return httpx.Response(200, json={"lemma": "reluctant", "senses": ["неохотный"], "simple": "disinclined"})

    client = LexoClient("https://lexo.test/", "tok", transport=httpx.MockTransport(rare))
    sense = asyncio.run(
        client.lookup(session_id="s" * 36, game=GameRef("G"), selection="reluctant", sentence="x", sentence_translation=None)
    )
    assert sense.simple == ""  # старый или ошибившийся сервер: редкое слово не показываем


def test_saved_word_keeps_simple(tmp_path):
    s = Store(tmp_path / "local.db")
    s.word_add(SavedWord("g.exe", "G", "rebuttal", "rebuttal", "опровержение", simple="argument against"))
    s.word_add(SavedWord("g.exe", "G", "go", "go", "идти"))
    words = {w.word: w for w in s.words_list()}
    assert words["rebuttal"].simple == "argument against" and words["go"].simple == ""
    s.close()
    again = Store(tmp_path / "local.db")  # миграция на старой базе не ломает открытие
    assert {w.word for w in again.words_list()} == {"rebuttal", "go"}
    again.close()


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_card_shows_simple_line(qapp):
    from lexoplay.geometry import Rect
    from lexoplay.ui.word_layer import WordLayer
    from lexoplay.wordmode import reader_views

    views = reader_views("Her rebuttal was perfect.")
    layer = WordLayer()
    layer.animate = False
    layer.open(Rect(0, 0, 1000, 700), Rect(0.05, 0.75, 0.9, 0.2), views)
    qapp.processEvents()
    layer.select([1])
    with_simple = CardState("ready", "rebuttal", senses=["опровержение", "контраргумент"], local=True, simple="argument against")
    without = CardState("ready", "rebuttal", senses=["опровержение", "контраргумент"], local=True)
    # строка с синонимом добавляет высоту карточке: значит, она нарисована
    from PySide6.QtGui import QImage, QPainter

    img = QImage(10, 10, QImage.Format.Format_ARGB32)
    p = QPainter(img)
    sel, bounds = Rect(100, 500, 80, 30), Rect(0, 0, 1000, 700)
    h1 = layer._paint_card(p, with_simple, sel, bounds, 16.0, Rect(0, 0, 0, 0), measure=True)  # noqa: SLF001
    h0 = layer._paint_card(p, without, sel, bounds, 16.0, Rect(0, 0, 0, 0), measure=True)  # noqa: SLF001
    p.end()
    assert h1 is not None and h0 is not None and h1[1] > h0[1]
    layer.finish("esc")
