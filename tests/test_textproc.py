from lexoplay.geometry import Rect
from lexoplay.ocr_model import Line, OcrResult, Word
from lexoplay.textproc import has_text, join_lines, to_utterance


def ocr(*lines: str) -> OcrResult:
    out = []
    for i, text in enumerate(lines):
        words = tuple(Word(w, Rect(0.1 * j, 0.1 * i, 0.08, 0.05)) for j, w in enumerate(text.split()))
        out.append(Line(words))
    return OcrResult(tuple(out))


def test_join_hyphen_wrap():
    assert join_lines(["I can't remem-", "ber his name."]) == "I can't remember his name."


def test_join_keeps_real_dash_and_capital():
    assert join_lines(["Wait--", "no."]) == "Wait-- no."
    assert join_lines(["Self-", "Made"]) == "Self- Made"


def test_speaker_on_own_line():
    u = to_utterance(ocr("Mira", "I was reluctant to come here", "tonight, you know."))
    assert u.speaker == "Mira"
    assert u.text == "I was reluctant to come here tonight, you know."


def test_speaker_colon():
    u = to_utterance(ocr("Old Man: Back again?"))
    assert u.speaker == "Old Man" and u.text == "Back again?"


def test_speaker_brackets():
    u = to_utterance(ocr("【Ren】 Sorry, the bus broke down."))
    assert u.speaker == "Ren" and u.text == "Sorry, the bus broke down."


def test_single_short_line_is_not_a_speaker():
    u = to_utterance(ocr("Hello"))
    assert u.speaker is None and u.text == "Hello"


def test_sentence_first_line_is_not_a_speaker():
    u = to_utterance(ocr("Yes.", "Let's go."))
    assert u.speaker is None and u.text == "Yes. Let's go."


def test_name_region_wins():
    u = to_utterance(ocr("I was reluctant."), name_text="Mira")
    assert u.speaker == "Mira" and u.text == "I was reluctant."


def test_has_text():
    assert not has_text("| .")
    assert has_text("Hi")


def test_sentence_start_is_not_a_speaker():
    from lexoplay.geometry import Rect
    from lexoplay.ocr_model import Line, OcrResult, Word

    def r(*lines):
        return OcrResult(tuple(Line(tuple(Word(w, Rect(0, 0, 0.1, 0.1)) for w in t.split())) for t in lines))

    assert to_utterance(r("HEY YOU", "GET OUT OF MY HOUSE")).speaker is None
    assert to_utterance(r("I know", "what you did.")).text == "I know what you did."
    assert to_utterance(r("MIRA", "GET OUT OF MY HOUSE")).speaker == "MIRA"
    assert to_utterance(r("Old Man", "Back again?")).speaker == "Old Man"


def test_same_text_twice_on_screen_is_read_once():
    from lexoplay.textproc import drop_repeats

    # Danganronpa: правило в рамке на фоне и тут же в окне диалога, переносы строк разные
    both = [
        "Students may reside only within the school.",
        "Leaving campus is an unacceptable use of time.",
        "Students may reside only within the school. Leaving",
        "campus is an unacceptable use of time.",
    ]
    once = "Students may reside only within the school. Leaving campus is an unacceptable use of time."
    assert drop_repeats(both) == once
    # копия на фоне прочитана хуже: берём чистую
    noisy = ["Students may reside only within the schooI.", "Leaving campus is an unacceptab1e use of time.", *both[2:]]
    assert drop_repeats(noisy) == once
    # повтор одного предложения внутри реплики
    assert drop_repeats(["Rule one: Students may reside only within the school.", "Students may reside only within the school. Got it?"]) == (
        "Rule one: Students may reside only within the school. Got it?"
    )
    # нарочные короткие повторы и обычный перенос не трогаем
    assert drop_repeats(["No! No! No!"]) == "No! No! No!"
    assert drop_repeats(["I said run. I said run."]) == "I said run. I said run."
    assert drop_repeats(["Before we go any farther, I guess I should", "introduce myself."]) == (
        "Before we go any farther, I guess I should introduce myself."
    )


def test_name_frame_name_is_not_part_of_the_line():
    """Окно диалога захватило плашку с именем, а имя уже читается из своей рамки."""
    u = to_utterance(ocr("Sakura Ogani", "Indeed... these are all the actions of an assailant."), "Sakura Ogami")
    assert u.speaker == "Sakura Ogami" and u.text.startswith("Indeed")
    u = to_utterance(ocr("Narrator But no matter how many weak spots,"), "Narrator")
    assert u.text == "But no matter how many weak spots,"
    u = to_utterance(ocr("Narrator: But no matter"), "Narrator")
    assert u.text == "But no matter"
    u = to_utterance(ocr("Sakura is strong."), "Sakura Ogami")  # имя внутри фразы не трогаем
    assert u.text == "Sakura is strong."


def test_interface_labels_are_not_part_of_the_line():
    """Danganronpa: «SPEAKING» в окне диалога, пока персонаж говорит."""
    u = to_utterance(ocr("Monokuma", "It's just a little present.", "SPEAKING"), "Monokuma")
    assert u.text == "It's just a little present."
    u = to_utterance(ocr("Monokuma", "It's just a little present.", "SPE ING"), "Monokuma")
    assert u.text == "It's just a little present."
    # на той же строке, но далеко справа
    words = [Word(w, Rect(0.05 + 0.07 * j, 0.8, 0.06, 0.04)) for j, w in enumerate("It's just a".split())]
    words.append(Word("SPEAKING", Rect(0.85, 0.8, 0.1, 0.04)))
    u = to_utterance(OcrResult((Line(tuple(words)),)))
    assert u.text == "It's just a"
    # рядом с текстом капс это часть реплики
    assert to_utterance(ocr("I love NYC")).text == "I love NYC"
    # имя капсом в первой строке остаётся именем
    u = to_utterance(ocr("MIRA", "I was reluctant to come here"))
    assert u.speaker == "MIRA"
    # реплика вся капсом: ничего не выкидываем
    assert to_utterance(ocr("YOU'RE NOT LEAVING", "THIS ROOM ALIVE")).text == "YOU'RE NOT LEAVING THIS ROOM ALIVE"
    # крик с восклицанием: это реплика
    assert to_utterance(ocr("Mira", "And then I said", "WHAT?!")).text == "And then I said WHAT?!"
