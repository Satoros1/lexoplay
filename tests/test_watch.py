"""Перевод пропадает только когда реплика сменилась, а не когда в окне диалога что-то мигнуло."""

from PIL import Image, ImageDraw

from lexoplay.geometry import Rect
from lexoplay.preprocess import thumbnail
from lexoplay.watch import LineWatcher

REGION = Rect(0.05, 0.75, 0.9, 0.2)
LINE = "...SUSPECTS CLAIM TO HAVE BEEN THREATENED INTO..."


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def frame(text: str, arrow: bool = False, bg: int = 0) -> Image.Image:
    img = Image.new("RGB", (800, 600), (bg, bg, bg))
    d = ImageDraw.Draw(img)
    d.text((60, 480), text, fill=(240, 230, 200))
    if arrow:  # мигающая стрелка «дальше» в углу окна диалога
        d.rectangle((690, 515, 720, 545), fill=(255, 255, 255))
    return img


def watcher(clock):
    return LineWatcher(LINE, thumbnail(frame(LINE), REGION), clock=clock)


def test_nothing_moved_no_ocr():
    clock = Clock()
    w = watcher(clock)
    assert not w.needs_ocr(thumbnail(frame(LINE), REGION))
    assert w.checks == 0


def test_blinking_arrow_keeps_translation():
    clock = Clock()
    w = watcher(clock)
    blink = thumbnail(frame(LINE, arrow=True), REGION)
    assert w.needs_ocr(blink)
    # OCR видит тот же текст (с обычной для пиксельного шрифта ошибкой)
    assert not w.line_changed("...SUSPECTS CLAlM TO HAVE BEEN THREATENED INT0...", blink)
    clock.t += 1
    assert not w.needs_ocr(blink)  # мигание стало новой точкой отсчёта
    clock.t += 1
    assert not w.line_changed(LINE, thumbnail(frame(LINE), REGION))


def test_next_line_hides_at_once():
    clock = Clock()
    w = watcher(clock)
    assert w.needs_ocr(thumbnail(frame("PREPARE TO DIE."), REGION))
    assert w.line_changed("PREPARE TO DIE.")


def test_typewriter_start_of_next_line_hides():
    w = watcher(Clock())
    assert w.line_changed("Wel")


def test_dialog_closed_needs_two_checks():
    w = watcher(Clock())
    assert not w.line_changed("")  # одна вспышка или переход экрана
    assert w.line_changed("")


def test_flash_then_same_text_resets():
    w = watcher(Clock())
    assert not w.line_changed("")
    assert not w.line_changed(LINE)
    assert not w.line_changed("")


def test_ocr_is_throttled():
    clock = Clock()
    w = watcher(clock)
    moving = thumbnail(frame(LINE, bg=90), REGION)
    assert w.needs_ocr(moving)
    clock.t += 0.2
    assert not w.needs_ocr(moving)
    clock.t += 0.3
    assert w.needs_ocr(moving)
    assert w.checks == 2


def test_without_player_action_nothing_is_checked():
    clock = Clock()
    w = LineWatcher(LINE, thumbnail(frame(LINE), REGION), gated=True, clock=clock)
    moving = thumbnail(frame(LINE, bg=120), REGION)  # фон «дышит» сам по себе
    assert not w.needs_ocr(moving)
    w.arm()  # игрок нажал клавишу
    assert w.acted and w.needs_ocr(moving)
    clock.t += 3.5  # прошло больше трёх секунд: снова спим
    assert not w.needs_ocr(moving)


def test_noisy_pixel_font_ocr_is_the_same_line():
    w = watcher(Clock())
    # OCR пиксельного шрифта: часть букв съехала, но слова те же
    assert not w.line_changed("SUSPECTS CLAIN T0 HAVE BEEM THREATENED IMTO")
    assert not w.line_changed("..SUSPECTS CLAIM TO HAUE BEEN THREATENED")


def test_verdicts_new_gone_and_grown():
    from PIL import Image

    from lexoplay.watch import LineWatcher

    base = Image.new("L", (40, 10), 0)
    w = LineWatcher("Welcome to the class trial.", base)
    assert w.check("Welcome to the class tria1.") is None
    assert w.check("Totally different words appear now") == "new"
    w2 = LineWatcher("Welcome to the", base)
    assert w2.check("Welcome to the class trial, everyone. Let us begin the debate now.") == "grown"  # допечаталась
    w3 = LineWatcher("Welcome to the class trial.", base)
    assert w3.check("") is None and w3.check("") == "gone"


def test_waiting_watcher_waits_for_new_text():
    from PIL import Image

    from lexoplay.watch import LineWatcher

    base = Image.new("L", (40, 10), 0)
    w = LineWatcher("Welcome to the class trial.", base, waiting=True)
    assert w.check("") is None and w.check("") is None  # окно диалога пустое: ждём
    assert w.check("|l") is None  # шум рамки
    assert w.check("Welcome to the class trial.") is None  # та же реплика снова
    assert w.check("Who could the killer be?") == "new"


def test_settle_window_catches_text_that_kept_typing():
    from PIL import Image

    from lexoplay.watch import LineWatcher

    t = [0.0]
    base = Image.new("L", (40, 10), 0)
    w = LineWatcher("Oh, and they mentioned the", base, gated=True, settle_for=3.0, clock=lambda: t[0])
    assert w.armed and not w.by_input
    # без действий игрока «другой текст» не прячет перевод (мигание, шум OCR)
    assert w.check("Something totally different here") is None
    assert w.check("Oh, and they mentioned the Ultimate Biker Gang Leader too.") == "grown"
    t[0] = 5.0
    assert not w.armed  # окно settle прошло, дальше только после действия игрока
