"""Режим слов: слово внутри выражения. Модель говорит, какое выражение вокруг слова и что учить,
карточка даёт выбор: выражение или само слово."""

import os

import pytest

from lexoplay.lookup import build_prompt, parse_sense, sense_from_json, sense_json
from lexoplay.wordmode import CardState

BUSH = (
    '{"lemma": "bush", "pos": "noun", "ipa": "bʊʃ", "senses": ["куст"], "marked_translation": "", '
    '"phrase": "beat around the bush", "phrase_lemma": "beat around the bush", '
    '"phrase_meaning": "ходить вокруг да около", "phrase_first": true}'
)


def test_lookup_asks_for_the_expression_around_the_word_and_parses_it():
    prompt = build_prompt("bush", "Don't beat around the bush.", "Не ходи вокруг да около.", "ru", "en")
    assert '"phrase"' in prompt and '"phrase_first"' in prompt
    assert "the same word the sentence translation above uses for it" in prompt  # значение как в переводе
    s = parse_sense(BUSH, "bush")
    assert (s.phrase, s.phrase_lemma, s.phrase_meaning, s.phrase_first) == (
        "beat around the bush", "beat around the bush", "ходить вокруг да около", True,
    )  # fmt: skip
    assert sense_from_json(sense_json(s), "bush") == s


def test_no_expression_when_the_whole_expression_is_selected_or_the_word_stands_alone():
    whole = parse_sense(
        '{"lemma": "beat around the bush", "senses": ["ходить вокруг да около"], "phrase": "beat around the bush", '
        '"phrase_meaning": "x", "phrase_first": true}',
        "beat around the bush",
    )
    assert whole.phrase == "" and not whole.phrase_first
    alone = parse_sense('{"lemma": "persuade", "senses": ["убедить"], "phrase": "", "phrase_first": true}', "persuade")
    assert alone.phrase == "" and not alone.phrase_first
    old = sense_from_json('{"lemma": "go", "senses": ["идти"]}', "go")  # кеш старых версий
    assert old is not None and old.phrase == ""


def test_card_buttons_for_expression_and_word():
    card = CardState("ready", "beat around the bush", pos="выражение", senses=["ходить вокруг да около"],
                     local=True, phrase_first=True, alt_label="само слово", alt_title="bush", alt_meaning="куст")  # fmt: skip
    assert card.button == "+ в мои слова" and card.alt_button == "+ слово"
    word = CardState("ready", "mind", senses=["ум"], alt_label="в выражении", alt_title="have the mind to do sth")
    assert word.alt_button == "+ выражение"
    assert CardState("loading", "mind", alt_title="x").alt_button is None
    saved = CardState("saved", "mind", alt_title="x", alt_label="в выражении")
    assert saved.button is None and saved.alt_button == "+ выражение"  # слово сохранил, выражение ещё можно
    assert CardState("ready", "mind", alt_title="x", alt_saved=True).alt_button is None


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_word_layer_shows_the_choice_and_saves_the_expression(qapp):
    from lexoplay.geometry import Rect
    from lexoplay.ui.word_layer import WordLayer
    from lexoplay.wordmode import reader_views

    views = reader_views("Don't beat around the bush. Just tell me.")
    layer = WordLayer()
    layer.animate = False
    alt, add = [], []
    layer.alt_clicked.connect(lambda: alt.append(1))
    layer.add_clicked.connect(lambda: add.append(1))
    layer.open(Rect(0, 0, 1000, 700), Rect(0.05, 0.75, 0.9, 0.2), views)
    qapp.processEvents()
    layer.select([4])  # bush
    layer.set_card(CardState("ready", "bush", senses=["куст"], local=True,
                             alt_label="в выражении", alt_title="beat around the bush", alt_meaning="ходить вокруг да около"))  # fmt: skip
    layer.set_phrase([1, 2, 3, 4])
    layer.grab()  # рисуется: карточка с выбором
    assert layer._alt_rect is not None and layer._button_rect is not None  # noqa: SLF001
    assert layer._alt_rect.top() > layer._button_rect.bottom()  # noqa: SLF001
    layer.click(layer._alt_rect.center())  # noqa: SLF001
    assert alt == [1] and add == []
    layer.set_card(CardState("ready", "bush", senses=["куст"], local=True, alt_label="в выражении",
                             alt_title="beat around the bush", alt_meaning="ходить вокруг да около", alt_saved=True))  # fmt: skip
    layer.grab()
    assert layer._alt_rect is None  # noqa: SLF001  уже сохранено: кнопки нет, «Тоже сохранено»
    layer.finish("esc")
