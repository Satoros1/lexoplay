from lexoplay.geometry import Rect, denormalize, fit_aspect, font_px_for, normalize, place_strip


def test_normalize_roundtrip():
    space = Rect(100, 50, 800, 600)
    r = Rect(140, 470, 720, 120)
    n = normalize(r, space)
    assert [round(v, 9) for v in (n.x, n.y, n.w, n.h)] == [0.05, 0.7, 0.9, 0.2]
    back = denormalize(n, space)
    assert [round(v, 6) for v in back.to_int()] == [140, 470, 720, 120]


def test_normalize_clamps_outside_space():
    n = normalize(Rect(-50, -50, 2000, 100), Rect(0, 0, 800, 600))
    assert n.x == 0 and n.y == 0 and n.w == 1


def test_from_points_any_direction():
    assert Rect.from_points(300, 200, 100, 50) == Rect(100, 50, 200, 150)


def test_fit_aspect_magpie_800x600_on_1440p():
    r = fit_aspect(800, 600, Rect(0, 0, 2560, 1440))
    assert r.to_int() == (320, 0, 1920, 1440)


def test_fit_aspect_wide_into_tall():
    r = fit_aspect(1920, 1080, Rect(0, 0, 1000, 1000))
    assert r.to_int() == (0, 219, 1000, 562)


def test_strip_goes_above_region_when_room():
    bounds = Rect(0, 0, 960, 720)
    region = Rect(40, 560, 880, 130)
    s = place_strip(region, 60, bounds)
    assert s.bottom == region.y - 8
    assert s.x == region.x and s.w == region.w


def test_strip_goes_below_when_region_at_top():
    bounds = Rect(0, 0, 960, 720)
    region = Rect(40, 10, 880, 130)
    s = place_strip(region, 60, bounds)
    assert s.y == region.bottom + 8


def test_strip_stays_inside_when_no_room():
    bounds = Rect(0, 0, 960, 200)
    region = Rect(0, 20, 960, 160)
    s = place_strip(region, 100, bounds)
    assert s.y == 20


def test_font_scales_with_game_height():
    assert font_px_for(1080) == 19
    assert round(font_px_for(1440), 2) == 25.33
    assert font_px_for(300) == 14  # не меньше 14
