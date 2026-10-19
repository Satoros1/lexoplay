from PIL import Image, ImageDraw

from lexoplay.geometry import Rect
from lexoplay.preprocess import changed, crop_norm, is_black, prepare, thumbnail, upscale_factor


def game_frame(text_box: str = "", dark: bool = False) -> Image.Image:
    img = Image.new("RGB", (800, 600), (30, 36, 51))
    d = ImageDraw.Draw(img)
    bg = (20, 20, 20) if dark else (250, 248, 242)
    fg = (240, 240, 240) if dark else (40, 40, 40)
    d.rectangle((40, 470, 760, 580), fill=bg)
    if text_box:
        d.text((60, 500), text_box, fill=fg)
    return img


REGION = Rect(0.05, 0.7833, 0.9, 0.1833)


def test_crop_matches_region():
    crop, r = crop_norm(game_frame("hello"), REGION)
    assert r.to_int() == (40, 470, 720, 110)
    assert crop.size == (720, 110)


def test_upscale_factor():
    assert upscale_factor(600) == 3.0
    assert round(upscale_factor(1080), 2) == 1.67
    assert upscale_factor(2160) == 1.0


def test_prepare_scales_and_maps_back():
    p = prepare(game_frame("hello"), REGION)
    assert p.image.size == (2160, 330) and p.scale == 3.0
    # левый верх подготовленной картинки = левый верх области
    n = p.to_norm(0, 0, 2160, 330)
    assert abs(n.x - 0.05) < 1e-6 and abs(n.w - 0.9) < 1e-6


def test_dark_background_gets_inverted():
    p = prepare(game_frame("hello", dark=True), REGION)
    # после инверсии фон светлый
    assert p.image.getpixel((5, 5)) > 200


def test_change_detection():
    a = thumbnail(game_frame("I was reluctant to come here tonight."), REGION)
    b = thumbnail(game_frame("I was reluctant to come here tonight."), REGION)
    c = thumbnail(game_frame("Sorry, the bus broke down halfway there."), REGION)
    assert not changed(a, b)
    assert changed(a, c)


def test_black_frame():
    assert is_black(Image.new("RGB", (100, 100)))
    assert not is_black(game_frame("x"))
