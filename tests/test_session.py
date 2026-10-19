"""Сессии: продолжение после быстрого перезапуска игры, конец по простою, по закрытию и при выходе."""

from lexoplay.session import IDLE_END_S, REOPEN_GRACE_S, Sessions


class Clock:
    def __init__(self) -> None:
        self.t = 10_000.0

    def __call__(self) -> float:
        return self.t


def make():
    ended = []
    clock = Clock()
    s = Sessions(lambda sess, dur, at: ended.append((sess.id, sess.exe, dur, at, sess.lines, sess.words)), clock)
    return s, clock, ended


def test_same_game_keeps_one_session():
    s, clock, ended = make()
    a = s.touch("Game.exe", "Game")
    clock.t += 60
    b = s.touch("game.exe", "Game")
    assert a.id == b.id and not ended
    s.line_translated("game.exe")
    s.word_added("GAME.EXE")
    assert (b.lines, b.words) == (1, 1)


def test_idle_half_hour_ends_session():
    s, clock, ended = make()
    a = s.touch("g.exe", "G")
    s.line_translated("g.exe")
    clock.t += 600
    s.touch("g.exe", "G")
    clock.t += IDLE_END_S + 1
    s.tick()
    assert len(ended) == 1
    sid, exe, dur, at, lines, _ = ended[0]
    assert sid == a.id and exe == "g.exe" and dur == 600 and lines == 1
    assert at == 10_600.0  # конец = последнее действие, а не момент проверки
    assert s.get("g.exe") is None


def test_touch_after_idle_starts_new_session():
    s, clock, ended = make()
    a = s.touch("g.exe", "G")
    clock.t += IDLE_END_S + 5
    b = s.touch("g.exe", "G")
    assert a.id != b.id and len(ended) == 1


def test_quick_restart_continues_session():
    s, clock, ended = make()
    a = s.touch("g.exe", "G")
    clock.t += 100
    s.game_closed("g.exe")
    clock.t += REOPEN_GRACE_S - 30
    s.tick()
    assert not ended
    b = s.touch("g.exe", "G")
    assert b.id == a.id and b.closing_since is None


def test_closed_game_ends_after_grace():
    s, clock, ended = make()
    s.touch("g.exe", "G")
    clock.t += 300
    s.game_closed("g.exe")
    clock.t += REOPEN_GRACE_S + 1
    s.tick()
    assert len(ended) == 1 and ended[0][2] == 300  # длительность до закрытия, без времени ожидания


def test_exit_ends_everything():
    s, clock, ended = make()
    s.touch("a.exe", "A")
    s.touch("b.exe", "B")
    clock.t += 50
    s.end_all()
    assert sorted(e[1] for e in ended) == ["a.exe", "b.exe"]
    assert s.get("a.exe") is None
