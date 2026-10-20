import pytest

from lexoplay.config import Settings
from lexoplay.hotkeys import MOD_CONTROL, MOD_NOREPEAT, MOD_SHIFT, HotkeyError, parse


def test_parse_plain_and_combo():
    assert parse("F2").vk == 0x71
    hk = parse("Ctrl+Shift+F2")
    assert hk.vk == 0x71 and hk.mods == MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT
    assert parse("ctrl + `").vk == 0xC0


def test_parse_errors():
    with pytest.raises(HotkeyError):
        parse("Hyper+F2")
    with pytest.raises(HotkeyError):
        parse("Ctrl+F99")


def test_settings_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("LEXOPLAY_HOME", str(tmp_path))
    s = Settings.load()
    s.target_lang = "uk"
    s.hotkeys.translate = "F6"
    s.no_focus_games = ["dr1_us.exe"]  # режим слов у этой игры фокус больше не берёт
    s.save()
    s2 = Settings.load()
    assert s2.target_lang == "uk" and s2.hotkeys.translate == "F6" and s2.no_focus_games == ["dr1_us.exe"]
    # мусорные ключи из старых версий не ломают загрузку
    p = Settings.path()
    p.write_text(p.read_text(encoding="utf-8").replace('"debug"', '"zzz": 1, "debug"'), encoding="utf-8")
    assert Settings.load().target_lang == "uk"


def test_normalize_and_risk():
    from lexoplay.hotkeys import normalize, risk

    assert normalize(" ctrl + f6 ") == "Ctrl+F6"
    assert normalize("alt+q") == "Alt+Q"
    assert normalize("pageup") == "PageUp" and normalize("num5") == "Num5"
    assert normalize("") == ""
    assert risk("F5") is None and risk("Alt+Q") is None
    assert "Ctrl" in risk("Ctrl+F2") and risk("Shift+F5")
    assert risk("Q") and risk("Space")


def test_old_ctrl_hotkeys_are_migrated(tmp_path, monkeypatch):
    import json

    monkeypatch.setenv("LEXOPLAY_HOME", str(tmp_path))
    Settings.path().write_text(
        json.dumps({"model": "m", "hotkeys": {"translate": "F2", "reselect": "Ctrl+F2", "add_game": "Ctrl+Shift+F2"}}),
        encoding="utf-8",
    )
    s = Settings.load()
    assert s.hotkeys.reselect == "F6" and s.hotkeys.add_game == "" and s.hotkeys.translate == "F2"
    s.hotkeys.reselect = "Ctrl+F2"  # выбрано руками в новой версии: не трогаем
    s.save()
    assert Settings.load().hotkeys.reselect == "Ctrl+F2"
