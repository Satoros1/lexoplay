import json
import zipfile


def test_report_has_logs_settings_and_no_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("LEXOPLAY_HOME", str(tmp_path))
    from lexoplay.config import Settings, data_dir
    from lexoplay.geometry import Rect
    from lexoplay.report import make_report
    from lexoplay.store import Game, Store

    (data_dir() / "lexoplay.log").write_text("2026 INFO F2 translated\n", encoding="utf-8")
    store = Store(tmp_path / "local.db")
    store.save_game(Game("dr1_us.exe", "Danganronpa", region=Rect(0.05, 0.7, 0.9, 0.25)))
    path = make_report(Settings(), store, extra={"linked": False}, dest=tmp_path)
    store.close()
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        assert {"info.json", "games.json", "settings.json", "logs/lexoplay.log"} <= names
        info = json.loads(z.read("info.json"))
        assert info["linked"] is False and info["version"]
        games = json.loads(z.read("games.json"))
        assert games[0]["exe"] == "dr1_us.exe" and games[0]["region"]["x"] == 0.05
        everything = b"".join(z.read(n) for n in names)
        assert b"sk-ant" not in everything and b"token" not in everything.lower()
