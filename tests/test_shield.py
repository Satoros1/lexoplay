"""Режим слов: какие клики и клавиши не пускаем в игру (логика хуков, без Windows)."""

from lexoplay.hotkeys import MOD_CONTROL, MOD_SHIFT
from lexoplay.shield import (
    VK_LBUTTON,
    VK_RBUTTON,
    VK_XBUTTON2,
    WM_KEYDOWN,
    WM_KEYUP,
    WM_LBUTTONDOWN,
    WM_LBUTTONUP,
    WM_MOUSEMOVE,
    WM_MOUSEWHEEL,
    WM_RBUTTONDOWN,
    WM_RBUTTONUP,
    WM_XBUTTONDOWN,
    WM_XBUTTONUP,
    Shield,
    button,
    needs_target,
)

ENTER, ESC, LEFT, SPACE = 0x0D, 0x1B, 0x25, 0x20
KEYS = {(ENTER, 0): "words_enter", (ESC, 0): "words_esc", (LEFT, 0): "words_left", (LEFT, MOD_SHIFT): "words_shift_left"}
REPEAT = frozenset({"words_left", "words_shift_left"})


def test_click_on_layer_never_reaches_game_even_its_release():
    s = Shield(active=True)
    assert s.mouse(WM_LBUTTONDOWN, over_layer=True) == (True, ("click", VK_LBUTTON))
    s.active = False  # клик по пустому месту закрыл режим ещё на нажатии
    assert not s.idle  # отпускание ещё ждём
    assert s.mouse(WM_LBUTTONUP) == (True, ("release", VK_LBUTTON))  # и его забираем: новеллы листают и по отпусканию
    assert s.idle
    assert s.mouse(WM_LBUTTONDOWN, over_layer=True) == (False, None)  # режим закрыт: клики снова игры
    assert s.mouse(WM_LBUTTONUP) == (False, None)


def test_clicks_outside_layer_and_moves_pass():
    s = Shield(active=True)
    assert s.mouse(WM_MOUSEMOVE, over_layer=True) == (False, None)
    assert s.mouse(WM_LBUTTONDOWN, over_layer=False) == (False, None)  # другое окно или монитор
    assert s.mouse(WM_LBUTTONUP) == (False, None)  # нажатие было не наше: отпускание тоже
    assert s.mouse(WM_RBUTTONDOWN, over_layer=True) == (True, ("click", VK_RBUTTON))
    assert s.mouse(WM_RBUTTONUP) == (True, ("release", VK_RBUTTON))
    assert s.mouse(WM_MOUSEWHEEL, over_layer=True) == (True, None)
    assert s.mouse(WM_MOUSEWHEEL, over_layer=False) == (False, None)
    s.active = False
    assert s.mouse(WM_MOUSEWHEEL, over_layer=True) == (False, None)


def test_side_buttons_are_ours_and_reported_for_hotkeys():
    s = Shield(active=True)
    x2 = 2 << 16
    assert button(WM_XBUTTONDOWN, x2) == (VK_XBUTTON2, True) and button(WM_XBUTTONDOWN, 7 << 16) is None
    assert s.mouse(WM_XBUTTONDOWN, x2, over_layer=True) == (True, ("button", VK_XBUTTON2))
    assert s.mouse(WM_XBUTTONUP, x2) == (True, None)
    assert needs_target(WM_XBUTTONDOWN) and needs_target(WM_MOUSEWHEEL) and not needs_target(WM_LBUTTONUP)


def test_missed_release_does_not_stick_a_button_in_the_game():
    s = Shield(active=True)
    s.mouse(WM_LBUTTONDOWN, over_layer=True)
    s.active = False  # отпускание до хука не дошло (например, Windows сняла хук)
    assert s.mouse(WM_LBUTTONDOWN, over_layer=True) == (False, None)  # новое нажатие уже игры
    assert s.mouse(WM_LBUTTONUP) == (False, None)  # и отпускание к нему тоже, иначе кнопка «залипнет»
    assert s.idle


def test_mode_keys_with_exact_modifiers_like_registerhotkey():
    s = Shield(active=True, keys=dict(KEYS), repeat=REPEAT)
    assert s.key(WM_KEYDOWN, ENTER) == (True, ("key", "words_enter"))
    assert s.key(WM_KEYDOWN, ENTER) == (True, None)  # автоповтор Enter: не сохраняем второй раз
    assert s.key(WM_KEYUP, ENTER) == (True, None)
    assert s.key(WM_KEYDOWN, ENTER, MOD_CONTROL) == (False, None)  # Ctrl+Enter не наша
    assert s.key(WM_KEYUP, ENTER, MOD_CONTROL) == (False, None)
    assert s.key(WM_KEYDOWN, SPACE) == (False, None)  # пробел не клавиша режима: игра листает дальше
    assert s.key(WM_KEYDOWN, LEFT) == (True, ("key", "words_left"))
    assert s.key(WM_KEYDOWN, LEFT) == (True, ("key", "words_left"))  # стрелку можно держать
    assert s.key(WM_KEYDOWN, LEFT, MOD_SHIFT) == (True, ("key", "words_shift_left"))  # зажали Shift на ходу
    assert s.key(WM_KEYUP, LEFT, MOD_SHIFT) == (True, None)
    assert s.held_keys == set()


def test_esc_closing_the_mode_keeps_its_repeats_and_release():
    s = Shield(active=True, keys=dict(KEYS), repeat=REPEAT)
    assert s.key(WM_KEYDOWN, ESC) == (True, ("key", "words_esc"))
    s.active = False  # Esc закрыл режим, а клавишу ещё держат
    assert s.key(WM_KEYDOWN, ESC) == (True, None)  # повтор не уходит в игру (там Esc открыл бы меню)
    assert s.key(WM_KEYUP, ESC) == (True, None)
    assert s.idle
    assert s.key(WM_KEYDOWN, ESC) == (False, None)  # дальше Esc снова игры
    assert s.key(WM_KEYUP, ESC) == (False, None)
