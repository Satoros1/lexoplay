from lexoplay.geometry import Rect
from lexoplay.store import Game, Store


def test_save_and_load(tmp_path):
    s = Store(tmp_path / "local.db")
    s.save_game(Game("MidnightStation.EXE", "Midnight Station", Rect(0.05, 0.75, 0.9, 0.2)))
    g = s.get_game("midnightstation.exe")
    assert g is not None
    assert g.title == "Midnight Station" and g.region == Rect(0.05, 0.75, 0.9, 0.2)
    assert g.name_region is None and g.capture is None

    s.save_game(g.with_(capture="screen", name_region=Rect(0.05, 0.7, 0.2, 0.04)))
    g2 = s.get_game("MIDNIGHTSTATION.exe")
    assert g2.capture == "screen" and g2.name_region is not None
    assert s.known_exes() == {"midnightstation.exe"}
    s.close()
