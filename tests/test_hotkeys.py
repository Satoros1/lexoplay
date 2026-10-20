

def test_mouse_buttons_can_be_hotkeys():
    from lexoplay.hotkeys import MOD_CONTROL, normalize, parse, risk

    hk = parse("Mouse5")
    assert hk.mouse and hk.vk == 0x06
    assert parse("mouse4").vk == 0x05 and parse("Mouse3").vk == 0x04
    ctrl = parse("Ctrl+Mouse5")
    assert ctrl.mouse and ctrl.mods & MOD_CONTROL
    assert normalize("ctrl + mouse5") == "Ctrl+Mouse5"
    assert not parse("F2").mouse
    assert "получит и игра" in (risk("Mouse5") or "")
