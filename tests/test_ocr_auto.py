from PIL import Image, ImageDraw

from lexoplay.geometry import Rect
from lexoplay.ocrscore import text_quality, token_score
from lexoplay.preprocess import otsu_level, prepare, value_gray


def test_clean_english_scores_high_and_junk_low():
    good = text_quality("You're not leaving this room, Jacket.")
    junk = text_quality("Y0u'r3 n0t |eav1ng th1s r00m")
    assert good.ok and good.ratio > 0.9
    assert not junk.ok and junk.ratio < 0.3
    assert good.rank > junk.rank


def test_token_rules():
    assert token_score("...")[0] == 0  # пунктуация не считается
    assert token_score("I")[1] == 1.0 and token_score("l")[1] < 0.5
    assert token_score("hElLo")[1] < 0.2  # регистр вразнобой: OCR
    assert token_score("HELLO")[1] == 1.0  # пиксельный шрифт капсом
    assert token_score("world's")[1] == 1.0 and token_score("well-known")[1] == 1.0
    assert token_score("wanted")[1] == 1.0 and token_score("cats")[1] == 1.0
    assert 0.5 < token_score("Monokuma")[1] < 1.0  # имя: не словарь, но и не мусор
    assert text_quality("My name's Makoto Naegi.").ok
    assert token_score("w@s")[1] == 0.0 and token_score("1999")[1] == 0.6


def test_other_language_only_checks_junk():
    q = text_quality("Je ne veux pas partir", "fr")
    assert q.ratio > 0.8


def colored_pixel_text(text: str) -> Image.Image:
    """Как в Hotline Miami: цветные пиксельные буквы на тёмном фоне."""
    small = Image.new("RGB", (220, 30), (12, 8, 30))
    d = ImageDraw.Draw(small)
    x = 4
    colors = [(40, 60, 255), (255, 30, 60), (30, 220, 255), (255, 0, 200)]
    for i, ch in enumerate(text):
        d.text((x, 8), ch, fill=colors[i % len(colors)])
        x += 7
    return small.resize((small.width * 4, small.height * 4), Image.Resampling.NEAREST)


def test_value_gray_keeps_blue_text_bright():
    img = Image.new("RGB", (4, 1), (0, 0, 255))
    assert value_gray(img).getpixel((0, 0)) == 255
    assert img.convert("L").getpixel((0, 0)) < 40  # обычная яркость: синий почти чёрный


def test_otsu_splits_two_levels():
    img = Image.new("L", (100, 10), 30)
    ImageDraw.Draw(img).rectangle((0, 0, 30, 10), fill=220)
    assert 30 <= otsu_level(img) < 220


def test_pixel_mode_is_black_text_on_white():
    frame = colored_pixel_text("HELLO THERE")
    prep = prepare(frame, Rect(0, 0, 1, 1), mode="pixel")
    data = prep.image.tobytes()
    assert set(data) <= {0, 255}
    assert data.count(255) / len(data) > 0.6  # фон светлый, буквы тёмные
    assert prep.scale == int(prep.scale) and prep.scale >= 2  # целое увеличение
    color = prepare(frame, Rect(0, 0, 1, 1), mode="color")
    assert color.image.mode == "L"


def test_old_binarize_flag_means_pixel():
    frame = colored_pixel_text("HI")
    a = prepare(frame, Rect(0, 0, 1, 1), binarize=True)
    b = prepare(frame, Rect(0, 0, 1, 1), mode="pixel")
    assert a.image.tobytes() == b.image.tobytes()
