"""Курсор держит игра: мышь двигают, а курсор стоит."""

from lexoplay.cursorguard import CursorGuard


def test_guard_notices_locked_cursor_once():
    pos = [(500, 300)]
    travel = [0]
    g = CursorGuard(lambda: pos[0], lambda: travel[0])
    g.reset()
    travel[0] += 40
    assert not g.tick()  # мышь почти не трогали
    pos[0] = (540, 300)
    travel[0] += 40
    assert not g.tick() and not g.locked  # курсор двигается вместе с мышью
    travel[0] += 200
    pos[0] = (541, 301)  # игра вернула его почти туда же
    assert g.tick() and g.locked  # двигали, а он стоит
    travel[0] += 500
    assert not g.tick()  # сообщаем один раз
    g.reset()
    assert not g.locked


def test_cursor_pinned_to_screen_edge_is_not_a_lock():
    pos = [(0, 300)]
    travel = [0]
    g = CursorGuard(lambda: pos[0], lambda: travel[0], counts=lambda p: p[0] > 0)  # x=0: край экрана
    g.reset()
    for _ in range(5):
        travel[0] += 300  # мышь толкают в край, курсор стоит
        assert not g.tick()
    assert not g.locked
