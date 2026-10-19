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
    s.save()
    s2 = Settings.load()
    assert s2.target_lang == "uk" and s2.hotkeys.translate == "F6"
    # мусорные ключи из старых версий не ломают загрузку
    p = Settings.path()
    p.write_text(p.read_text(encoding="utf-8").replace('"debug"', '"zzz": 1, "debug"'), encoding="utf-8")
    assert Settings.load().target_lang == "uk"
