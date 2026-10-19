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


# ---------------------------------------------------------------- настоящий SDK на поддельном сервере
def _http():
    """SDK 1.x ходит через httpx2 (у старых версий был httpx): берём то, на чём он работает."""
    pytest.importorskip("anthropic")
    try:
        import httpx2 as http
    except ImportError:
        import httpx as http
    return http


def _sdk_translator(handler):
    import anthropic

    from lexoplay.translate import AnthropicTranslator

    http = _http()
    tr = AnthropicTranslator("sk-test", "claude-haiku-5-5")
    tr._client = anthropic.AsyncAnthropic(  # noqa: SLF001
        api_key="sk-test", max_retries=0, http_client=http.AsyncClient(transport=http.MockTransport(handler))
    )
    return tr


def test_real_sdk_call_shape_and_parse():
    import asyncio
    import json as _json

    httpx = _http()
    seen = {}

    def handler(req):
        seen["body"] = _json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-haiku-5-5",
                "content": [{"type": "text", "text": '{"source": "I was", "translation": "Я был — тут"}'}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

    from lexoplay.translate import TranslateRequest

    res = asyncio.run(_sdk_translator(handler).translate(TranslateRequest("l was", "Makoto", "Danganronpa")))
    assert res.source == "I was" and res.translation == "Я был, тут"
    body = seen["body"]
    assert "temperature" not in body and "top_p" not in body  # новые модели отвечают на них 400
    assert body["model"] == "claude-haiku-5-5" and body["max_tokens"] == 400
    assert "Danganronpa" in body["system"] and "Makoto: l was" in body["messages"][0]["content"]


def test_real_sdk_errors_are_readable():
    import asyncio

    from lexoplay.translate import TranslateRequest, TranslationError

    httpx = _http()

    def bad_key(req):
        return httpx.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}})

    with pytest.raises(TranslationError) as e:
        asyncio.run(_sdk_translator(bad_key).translate(TranslateRequest("Hi", None, "G")))
    assert e.value.code == "bad_key" and "Ключ" in str(e.value)

    def deprecated(req):
        return httpx.Response(
            400, json={"type": "error", "error": {"type": "invalid_request_error", "message": "temperature is deprecated"}}
        )

    with pytest.raises(TranslationError) as e2:
        asyncio.run(_sdk_translator(deprecated).translate(TranslateRequest("Hi", None, "G")))
    assert "отклонил" in str(e2.value)
