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
    assert "first person" in system and "never retold" in system and "→" in system  # образцы перевода
    user = build_user(req)
    assert '<line speaker="Mira">You\'re late.</line>\n<translation>Ты опоздал.</translation>' in user
    assert user.strip().endswith('Translate this line:\n<line speaker="Old Man">Back again?</line>')
    glossed = build_user(TranslateRequest(text="Hi", speaker=None, game="g", glossary={"Monokuma": "Монокума"}))
    assert "<glossary>\nMonokuma => Монокума\n</glossary>" in glossed and glossed.endswith("<line>Hi</line>")
    assert clean_translation("<translation>Привет.</translation>") == "Привет."
    assert clean_translation("<transl", partial=True) == ""


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


def test_streams_translation_with_medium_effort_and_fixed_source():
    http = _http()
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        return _ok(
            http, _sse("Подозреваемые ", "утверждают, что им ", "угрожали…\n@", "@ ...Suspects claim to have been threatened into...")
        )

    parts = []
    req = TranslateRequest("...SUSPECTS CLAIM TO HAVE BEEN THREATENED INT0...", None, "Hotline Miami", on_partial=parts.append)
    res = asyncio.run(_sdk_translator(handler).translate(req))
    assert res.translation == "Подозреваемые утверждают, что им угрожали…"
    assert res.source == "...Suspects claim to have been threatened into..."  # оригинал без ошибок OCR, для режима слов
    # первые слова ждут, пока не станет ясно, что это не подпись «Имя:»
    assert parts[0].startswith("Подозреваемые утверждают") and not any("@" in x or "Suspects" in x for x in parts)
    body = seen[0]
    assert body["output_config"] == {"effort": "medium"} and "thinking" not in body  # немного думает перед ответом
    assert body["stream"] is True and body["max_tokens"] >= 4096  # thinking тратит те же max_tokens
    assert "temperature" not in body and "top_p" not in body
    assert "Hotline Miami" in body["system"][0]["text"] and body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "@@" in body["system"][0]["text"]


def test_without_thinking_and_strange_source_is_ignored():
    import anthropic

    from lexoplay.translate import AnthropicTranslator, pick_source

    http = _http()
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        return _ok(http, _sse("Привет.\n@@ Something totally different here, not the line at all."))

    tr = AnthropicTranslator("sk-test", "claude-haiku-5-5", think=False)
    tr._client = anthropic.AsyncAnthropic(  # noqa: SLF001
        api_key="sk-test", max_retries=0, http_client=http.AsyncClient(transport=http.MockTransport(handler))
    )
    res = asyncio.run(tr.translate(TranslateRequest("Hi there.", None, "G")))
    assert res.translation == "Привет." and res.source == "Hi there."  # чужой текст после @@ не берём
    assert seen[0]["thinking"] == {"type": "disabled"} and "output_config" not in seen[0]
    assert pick_source("l can't be|ieve it", "I can't believe it") == "I can't believe it"
    assert pick_source("Hi", None) == "Hi"


def test_thinking_block_and_json_habit_are_handled():
    http = _http()

    def handler(req):
        return _ok(http, _sse('{"source": "Hi", "translation": "Привет"}', thinking="the user wants..."))

    res = asyncio.run(_sdk_translator(handler).translate(TranslateRequest("Hi", None, "G")))
    assert res.translation == "Привет"


def test_falls_back_without_effort_then_to_low_effort():
    http = _http()
    seen = []

    def handler(req):
        body = json.loads(req.content)
        seen.append(body)
        if "output_config" in body and body["output_config"].get("effort") == "medium":
            return http.Response(
                400, json={"type": "error", "error": {"type": "invalid_request_error", "message": "effort is not supported"}}
            )
        if "thinking" in body:
            return http.Response(
                400,
                json={"type": "error", "error": {"type": "invalid_request_error", "message": "thinking.type disabled is not supported"}},
            )
        return _ok(http, _sse("Готово"))

    tr = _sdk_translator(handler)
    res = asyncio.run(tr.translate(TranslateRequest("Done", None, "G")))
    assert res.translation == "Готово"
    assert seen[1]["thinking"] == {"type": "disabled"} and "output_config" not in seen[1]  # без effort
    assert seen[2]["output_config"] == {"effort": "low"} and "thinking" not in seen[2]
    asyncio.run(tr.translate(TranslateRequest("Again", None, "G")))
    assert seen[3]["output_config"] == {"effort": "low"}  # запомнили, больше не пробуем


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


def test_translation_ending_follows_the_original():
    from lexoplay.translate import fix_punctuation, no_dash

    cases = [
        ("Oh, and they mentioned the", "А, и они упомянули,", "А, и они упомянули..."),  # фраза продолжится
        ("I wanted to tell you,", "Я хотел сказать тебе,", "Я хотел сказать тебе..."),
        ("Well, I guess...", "Ну, наверное", "Ну, наверное..."),  # потерянное многоточие
        ("Well, I guess...", "Ну, наверное.", "Ну, наверное..."),
        ("Well, I guess...", "Ну, наверное...", "Ну, наверное..."),
        ("You came back.", "Ты вернулся", "Ты вернулся."),  # потерянная точка
        ("You came back?", "Ты вернулся", "Ты вернулся?"),
        ("What?!", "Что", "Что?!"),
        ("I wanted to—", no_dash("Я хотел —"), "Я хотел..."),  # оборвалась речь: не запятая
        ("My view became warped, twisted.", "Мой... исказился, перекособочился,", "Мой... исказился, перекособочился."),
        ('They mentioned the "Leader" too.', "Ещё они упомянули «Лидера»", "Ещё они упомянули «Лидера»."),
        ("Makoto Naegi", "Макото Наэги", "Макото Наэги"),  # без знаков в оригинале: не трогаем
        ("Fine.", "Ладно, ", "Ладно."),
    ]
    for source, translation, expected in cases:
        assert fix_punctuation(source, translation) == expected, (source, translation)
    assert "never with a comma" in build_system(TranslateRequest(text="x", speaker=None, game="g"))


def test_thinking_out_loud_and_name_label_are_cut():
    """Danganronpa, 0.6.13: модель рассуждала про OCR прямо в ответе и подписала реплику именем."""
    from lexoplay.translate import drop_thinking, pick_source

    raw = (
        "Сакура Огами: Все её действия... были ничем иным, как...\n\n"
        'Wait, I need to carefully reconstruct the OCR text. "Sakura Ogani are all tip actbns of an", '
        'likely "Sakura Ogami: Are all of those actions an". I\'ll reconstruct as "That\'s all the actions of an".\n\n'
        "Сакура Огами: Это все действия...\n@@ These are all the actions of an..."
    )
    src = "are all tip actbns of an"
    assert clean_translation(raw, "Sakura Ogami", source=src) == "Это все действия..."
    # пока пишется: рассуждение на экран не попадает ни на миг
    for n in range(20, len(raw.split("@@")[0]), 7):
        part = clean_translation(raw[:n], "Sakura Ogami", partial=True, source=src)
        assert "OCR" not in part and "Wait" not in part and "Сакура" not in part, part
    # одна строка с именами латиницей: это перевод, не рассуждение
    assert drop_thinking("Monokuma, Junko Enoshima и Mukuro Ikusaba здесь.") == "Monokuma, Junko Enoshima и Mukuro Ikusaba здесь."
    assert clean_translation("OK, я понял.", None, source="OK, I got it.") == "OK, я понял."
    assert clean_translation("Ich weiß es nicht.\nWait, the OCR says...\nIch weiß nicht.", None, target_lang="de", source="x") == "Ich weiß nicht."
    # подпись «Имя:» убираем, только если в оригинале её не было
    assert clean_translation("Рассказчик: Но слабых мест много.", "Narrator", source="But there are many weak spots.") == "Но слабых мест много."
    assert clean_translation("Смотри: вот тут.", None, source="Look: right here.") == "Смотри: вот тут."
    assert clean_translation("смотри, вот тут.", "Mira", source="Look here.") == "смотри, вот тут."
    # и в исправленном оригинале тоже
    assert pick_source("But no matter how many weak spots", "Narrator: But no matter how many weak spots.").startswith("But no")
    assert pick_source("Note: keep out", "Note: Keep out.") == "Note: Keep out."


def test_prompt_forbids_reasoning_in_reply():
    system = build_system(TranslateRequest(text="x", speaker=None, game="G"))
    assert "never contains your reasoning" in system
