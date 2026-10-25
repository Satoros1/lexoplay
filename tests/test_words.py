"""Слова для F3: формы, служебные слова, выбор фразы, кликабельность и место карточки."""

from lexoplay.geometry import Rect
from lexoplay.ocr_model import Line, OcrResult, Word
from lexoplay.store import Store
from lexoplay.vocab import Vocab
from lexoplay.wordmode import CardState, build_views, place_card, selected_text, selection_range
from lexoplay.words import clean, forms, is_stopword, is_word


def test_clean_strips_edges_keeps_inner_apostrophe():
    assert clean('"Hello,') == "Hello"
    assert clean("dark...") == "dark"
    assert clean("(won’t)") == "won't"
    assert clean("—") == ""


def test_forms():
    assert "run" in forms("running")
    assert "stop" in forms("stopped")
    assert "make" in forms("making")
    assert "city" in forms("cities")
    assert "carry" in forms("carried")
    assert "be" in forms("was")
    assert "do" in forms("Did")
    assert "child" in forms("children")
    assert "happy" in forms("happier")
    assert "quick" in forms("quickly")
    assert "mira" in forms("Mira's")
    assert "eerie" in forms("eerie")
    assert "eerie" in forms("Eerie,")


def test_stopwords():
    assert is_stopword("The") and is_stopword("you,") and not is_stopword("reluctant")
    assert is_word("I'm") and not is_word("...") and not is_word("42")


def ocr(*lines: str) -> OcrResult:
    out = []
    y = 0.8
    for text in lines:
        words = []
        x = 0.1
        for w in text.split():
            words.append(Word(w, Rect(x, y, 0.01 * len(w), 0.03)))
            x += 0.01 * len(w) + 0.01
        out.append(Line(tuple(words)))
        y += 0.05
    return OcrResult(tuple(out))


def test_views_mark_known_stop_and_punctuation():
    res = ocr("I was reluctant to come here", "This place feels eerie ...")
    known = {"eerie", "come"}
    views = build_views(res, lambda w: any(f in known for f in forms(w)))
    by = {v.text: v for v in views}
    assert by["eerie"].known and not by["eerie"].stop
    assert by["I"].stop and by["to"].stop and not by["to"].known
    assert by["come"].stop  # служебное: не подчёркиваем, даже если есть в Lexo
    assert not by["reluctant"].known and by["reluctant"].clickable
    assert not by["..."].clickable


def test_phrase_selection_limits_and_text():
    assert selection_range(None, 4) == [4]
    assert selection_range(2, 4) == [2, 3, 4]
    assert selection_range(5, 2) == [2, 3, 4, 5]
    assert selection_range(0, 9) == [0, 1, 2, 3, 4]  # не больше 5 слов
    res = ocr("You must make amends, Mira.")
    views = build_views(res)
    assert selected_text(views, [2, 3]) == "make amends"
    assert selected_text(views, [4]) == "Mira"


def test_card_goes_below_or_above_and_stays_inside():
    bounds = Rect(0, 0, 1000, 800)
    below = place_card(Rect(100, 300, 60, 20), 300, 200, bounds)
    assert below.y > 320 and below.x == 100
    above = place_card(Rect(100, 700, 60, 20), 300, 200, bounds)
    assert above.bottom <= 700
    right = place_card(Rect(950, 300, 40, 20), 300, 200, bounds)
    assert right.right <= 992
    huge = place_card(Rect(100, 400, 60, 20), 300, 790, bounds)
    assert huge.y >= 8
    # диалог внизу: карточка над ним и над строкой перевода, ничего не закрывая
    dialog_and_strip = Rect(60, 470, 880, 320)
    up = place_card(Rect(300, 650, 60, 20), 300, 200, bounds, avoid=dialog_and_strip)
    assert up.bottom <= 470 and up.x == 300
    # диалог вверху: карточка под ним
    top = place_card(Rect(300, 60, 60, 20), 300, 200, bounds, avoid=Rect(60, 20, 880, 200))
    assert top.y >= 220


def test_card_buttons():
    assert CardState("ready", "eerie").button == "+ в Lexo"
    assert CardState("ready", "eerie", in_deck=True).button == "+ пример из игры"
    assert CardState("saved", "eerie").button is None
    assert not CardState("loading", "eerie").button_enabled
    assert CardState("offline", "eerie").button_enabled
    assert CardState("ready", "eerie", ipa="ˈɪəri", pos="adj").subtitle == "/ˈɪəri/ · adj"


def test_vocab_known_and_persisted(tmp_path):
    store = Store(tmp_path / "local.db")
    v = Vocab(store)
    v.replace(["eerie", "Make Amends", "run"], "2026-10-19T10:00:00Z")
    assert v.known("Eerie.") and v.known("running") and not v.known("reluctant")
    v.add("reluctant")
    assert v.known("reluctant")
    v.merge(["stroll"], "2026-10-19T11:00:00Z")
    assert v.since == "2026-10-19T11:00:00Z"
    store.close()
    store2 = Store(tmp_path / "local.db")
    v2 = Vocab(store2)
    assert v2.known("strolled") and v2.known("reluctantly") and "make amends" in store2.vocab_load()
    v2.clear()
    assert len(v2) == 0 and v2.since is None
    store2.close()


def test_forms_do_not_turn_common_words_into_rare_ones():
    """number не форма numb, corner не corn, morning не morn: частое слово остаётся собой."""
    for word, wrong in [
        ("number", "numb"), ("corner", "corn"), ("hammer", "ham"), ("butter", "butt"), ("bitter", "bit"),
        ("morning", "morn"), ("wedding", "wed"), ("sometimes", "sometime"), ("early", "ear"), ("apply", "app"),
    ]:
        assert wrong not in forms(word), word
    for word, base in [
        ("numbers", "number"), ("numbed", "numb"), ("happier", "happy"), ("bigger", "big"), ("faster", "fast"),
        ("kids", "kid"), ("eerily", "eerie"), ("quickly", "quick"), ("terribly", "terrible"), ("stuck", "stick"),
        ("lying", "lie"), ("deeply", "deep"),
    ]:
        assert base in forms(word), word


def test_vocab_does_not_see_numb_in_number(tmp_path):
    from lexoplay.recall import RecallIndex
    from lexoplay.store import SavedWord, Store
    from lexoplay.vocab import Vocab

    v = Vocab(Store(tmp_path / "db.sqlite"))
    v.add("numb")
    assert v.known("numbed") and not v.known("number")  # F3 не подчёркивает number жёлтым
    idx = RecallIndex([SavedWord("g", "G", "numb", None, "онемевший", id=1, created_at=1.0)])
    line = "The true killer manipulated Hifumi to carry out a number of actions, and in the end...murdered him."
    assert idx.find(line) == []  # и подсказки «numb · онемевший» под переводом нет
    assert [e.word for e in idx.find("My hands went numb.")] == ["numb"]
