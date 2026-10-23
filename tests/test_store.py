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


def test_old_database_gets_deck_columns(tmp_path):
    import sqlite3

    path = tmp_path / "local.db"
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE games (exe TEXT PRIMARY KEY, title TEXT NOT NULL, region_json TEXT, name_region_json TEXT,"
        " capture TEXT, binarize INTEGER NOT NULL DEFAULT 0, last_played_at REAL);"
        "INSERT INTO games (exe, title) VALUES ('dr1_us.exe', 'Danganronpa');"
    )
    db.commit()
    db.close()
    from lexoplay.store import Store

    s = Store(path)
    g = s.get_game("dr1_us.exe")
    assert g.title == "Danganronpa" and g.deck_mode == "game" and g.deck_label == "«Danganronpa»"
    s.save_game(g.with_(deck_mode="deck", deck_id=7, deck_title="Слова из игр"))
    g2 = s.get_game("DR1_US.EXE")
    assert g2.deck_id == 7 and g2.deck_label == "«Слова из игр»"
    s.close()


def test_games_filter():
    from lexoplay.games import WindowInfo, clean_title, is_game_exe, rank

    assert not is_game_exe("chrome.exe") and not is_game_exe("SteamWebHelper.exe") and is_game_exe("dr1_us.exe")
    wins = [
        WindowInfo(1, "Steam", "steamwebhelper.exe", "", 1200, 800),
        WindowInfo(2, "Danganronpa: Trigger Happy Havoc", "dr1_us.exe", "", 1920, 1080),
        WindowInfo(3, "Small tool", "tool.exe", "", 300, 200),
        WindowInfo(4, "Hidden VN", "vn.exe", "", 1280, 720, minimized=True),
    ]
    assert [w.hwnd for w in rank(wins)] == [2, 3, 4]
    assert clean_title("  My Game - Direct3D 9 ", "mygame.exe") == "My Game"
    assert clean_title("", "mygame.exe") == "mygame"


def test_game_notes_roundtrip(tmp_path):
    from lexoplay.store import Store

    s = Store(tmp_path / "db.sqlite")
    assert s.game_notes("dr1_us.exe", "ru") is None
    s.game_notes_set("DR1_US.exe", "ru", "GAME: Danganronpa")
    rec = s.game_notes("dr1_us.exe", "ru")
    assert rec is not None and rec.text == "GAME: Danganronpa" and rec.status == "ready" and not rec.edited
    s.game_notes_set("dr1_us.exe", "ru", "", "off", edited=True)
    rec = s.game_notes("dr1_us.exe", "ru")
    assert rec is not None and rec.status == "off" and rec.edited
    assert s.game_notes("dr1_us.exe", "en") is None  # у другого языка своя справка
