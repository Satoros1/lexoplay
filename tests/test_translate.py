import pytest

from lexoplay.translate import ContextLine, TranslateRequest, build_system, build_user, parse_reply


def test_parse_plain_json():
    r = parse_reply('{"source": "I was reluctant.", "translation": "Мне не хотелось."}', "l was reluctant.")
    assert r.source == "I was reluctant." and r.translation == "Мне не хотелось."


def test_parse_json_inside_text():
    r = parse_reply('Sure!\n{"source": "", "translation": "Привет"}\n', "Hi")
    assert r.source == "Hi" and r.translation == "Привет"


def test_parse_rejects_empty():
    with pytest.raises(ValueError):
        parse_reply('{"translation": ""}', "x")
    with pytest.raises(ValueError):
        parse_reply("no json here", "x")


def test_prompt_contains_context_and_speaker():
    req = TranslateRequest(
        text="Back again?",
        speaker="Old Man",
        game="Midnight Station",
        context=(ContextLine("Mira", "You're late.", "Ты опоздал."),),
    )
    system = build_system(req)
    assert "Russian" in system and "Midnight Station" in system
    user = build_user(req)
    assert "Mira: You're late. => Ты опоздал." in user
    assert user.strip().endswith("Old Man: Back again?")
