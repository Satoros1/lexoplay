import asyncio
import json

import pytest

from lexoplay.translate import (
    ContextLine,
    TranslateRequest,
    TranslationError,
    build_system,
    build_user,
    clean_translation,
    parse_reply,
)


def test_parse_json_reply_still_works():
    r = parse_reply('Sure!\n{"source": "I was", "translation": "Я был — тут"}\n', "l was")
    assert r.source == "I was" and r.translation == "Я был, тут"
    with pytest.raises(ValueError):
        parse_reply('{"translation": ""}', "x")


def test_clean_translation():
    assert clean_translation("«Подозреваемые утверждают»") == "Подозреваемые утверждают"
    assert clean_translation("Перевод: Привет — друг") == "Привет, друг"
    assert clean_translation("Makoto: Меня зовут Макото.", "Makoto") == "Меня зовут Макото."
    assert clean_translation('```json\n{"source": "Hi", "translation": "Привет"}\n```') == "Привет"
    assert clean_translation('{"translation": "При', partial=True) == ""  # JSON ещё дописывается
    assert clean_translation('"Подозрева', partial=True) == "Подозрева"


def test_prompt_contains_context_and_speaker():
    req = TranslateRequest(
        text="Back again?",
        speaker="Old Man",
        game="Midnight Station",
        context=(ContextLine("Mira", "You're late.", "Ты опоздал."),),
    )
    system = build_system(req)
    assert "Russian" in system and "Midnight Station" in system and "ALL CAPS" in system
    assert "JSON" not in system  # ответ просто текстом: разбирать нечего
    user = build_user(req)
    assert "Mira: You're late. => Ты опоздал." in user
    assert user.strip().endswith("Translate:\nOld Man: Back again?")


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


def _sse(*texts, thinking=None, stop="end_turn"):
    """Ответ Messages API потоком: при желании сначала блок thinking, потом текст кусками."""
    ev = []

    def add(name, data):
        ev.append(f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n")

    add(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-haiku-5-5",
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 1},
            },
        },
    )
    idx = 0
    if thinking is not None:
        add("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}})
        add("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": thinking}})
        add("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig"}})
        add("content_block_stop", {"type": "content_block_stop", "index": 0})
        idx = 1
    if texts:
        add("content_block_start", {"type": "content_block_start", "index": idx, "content_block": {"type": "text", "text": ""}})
        for t in texts:
            add("content_block_delta", {"type": "content_block_delta", "index": idx, "delta": {"type": "text_delta", "text": t}})
        add("content_block_stop", {"type": "content_block_stop", "index": idx})
    add("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None}, "usage": {"output_tokens": 12}})
    add("message_stop", {"type": "message_stop"})
    return "".join(ev).encode()


def _ok(http, body):
    return http.Response(200, headers={"content-type": "text/event-stream"}, content=body)


def test_streams_translation_without_thinking_or_temperature():
    http = _http()
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        return _ok(http, _sse("Подозреваемые ", "утверждают, что им ", "угрожали…"))

    parts = []
    req = TranslateRequest("...SUSPECTS CLAIM TO HAVE BEEN THREATENED INTO...", None, "Hotline Miami", on_partial=parts.append)
    res = asyncio.run(_sdk_translator(handler).translate(req))
    assert res.translation == "Подозреваемые утверждают, что им угрожали…"
    assert res.source == req.text
    assert parts[0] == "Подозреваемые" and parts[-1].startswith("Подозреваемые утверждают")
    body = seen[0]
    assert body["thinking"] == {"type": "disabled"}  # Haiku 5.x иначе сначала думает
    assert body["stream"] is True and body["max_tokens"] >= 1024
    assert "temperature" not in body and "top_p" not in body
    assert "Hotline Miami" in body["system"]


def test_thinking_block_and_json_habit_are_handled():
    http = _http()

    def handler(req):
        return _ok(http, _sse('{"source": "Hi", "translation": "Привет"}', thinking="the user wants..."))

    res = asyncio.run(_sdk_translator(handler).translate(TranslateRequest("Hi", None, "G")))
    assert res.translation == "Привет"


def test_falls_back_to_low_effort_if_thinking_cannot_be_disabled():
    http = _http()
    seen = []

    def handler(req):
        body = json.loads(req.content)
        seen.append(body)
        if "thinking" in body:
            return http.Response(
                400,
                json={"type": "error", "error": {"type": "invalid_request_error", "message": "thinking.type disabled is not supported"}},
            )
        return _ok(http, _sse("Готово"))

    tr = _sdk_translator(handler)
    res = asyncio.run(tr.translate(TranslateRequest("Done", None, "G")))
    assert res.translation == "Готово"
    assert seen[1]["output_config"] == {"effort": "low"} and "thinking" not in seen[1]
    asyncio.run(tr.translate(TranslateRequest("Again", None, "G")))
    assert "thinking" not in seen[2]  # запомнили, больше не пробуем


def test_only_thinking_and_cut_off_gives_clear_error():
    http = _http()

    def handler(req):
        return _ok(http, _sse(thinking="hmm", stop="max_tokens"))

    with pytest.raises(TranslationError) as e:
        asyncio.run(_sdk_translator(handler).translate(TranslateRequest("Hi", None, "G")))
    assert "не прислала" in str(e.value)


def test_real_sdk_errors_are_readable():
    http = _http()

    def bad_key(req):
        return http.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}})

    with pytest.raises(TranslationError) as e:
        asyncio.run(_sdk_translator(bad_key).translate(TranslateRequest("Hi", None, "G")))
    assert e.value.code == "bad_key" and "Ключ" in str(e.value)

    def deprecated(req):
        return http.Response(
            400, json={"type": "error", "error": {"type": "invalid_request_error", "message": "temperature is deprecated"}}
        )

    with pytest.raises(TranslationError) as e2:
        asyncio.run(_sdk_translator(deprecated).translate(TranslateRequest("Hi", None, "G")))
    assert "отклонил" in str(e2.value)
