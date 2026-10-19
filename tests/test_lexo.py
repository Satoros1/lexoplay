"""Клиент Lexo на поддельном транспорте httpx: запросы, ответы и ошибки сервера."""

import asyncio
import json

import httpx
import pytest

from lexoplay import __version__
from lexoplay.lexo import GameRef, LexoClient, LexoError, LexoOffline
from lexoplay.translate import LexoTranslator, TranslateRequest, TranslationError


def client_with(handler, token="tok") -> LexoClient:
    return LexoClient("https://lexo.test/", token, transport=httpx.MockTransport(handler))


def run(coro):
    return asyncio.run(coro)


def test_link_sends_code_and_keeps_token():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["path"] = req.url.path
        seen["body"] = json.loads(req.content)
        seen["auth"] = req.headers.get("authorization")
        seen["version"] = req.headers.get("x-lexoplay-version")
        return httpx.Response(
            200,
            json={
                "token": "secret",
                "device_id": 3,
                "user": {"username": "ruslan", "native_lang": "ru", "learning_lang": "en", "premium": False},
            },
        )

    c = client_with(handler, token=None)
    token, acc = run(c.link(" k7qx2m ", "RUSLAN-PC"))
    assert token == "secret" and c.token == "secret" and c.linked
    assert seen["path"] == "/desktop/link"
    assert seen["body"]["code"] == "K7QX2M" and seen["body"]["device_name"] == "RUSLAN-PC"
    assert seen["auth"] is None  # код привязки идёт без токена
    assert seen["version"] == __version__
    assert acc.label == "@ruslan" and acc.native_lang == "ru"


def test_bad_code_error_carries_server_message():
    def handler(req):
        return httpx.Response(400, json={"detail": {"code": "bad_code", "message": "Код не подошёл."}})

    with pytest.raises(LexoError) as e:
        run(client_with(handler, None).link("AAAAAA", "pc"))
    assert e.value.code == "bad_code" and e.value.message == "Код не подошёл." and not e.value.retryable


def test_translate_request_shape_and_usage():
    seen = {}

    def handler(req):
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(
            200, json={"source": "I was", "translation": "Я был", "cached": False, "usage": {"used": 3, "limit": 50}}
        )

    src, tr, cached, usage = run(
        client_with(handler).translate(
            session_id="s" * 36,
            line_id="l" * 36,
            game=GameRef("Midnight Station", "ms.exe"),
            text="l was",
            speaker="Mira",
            context=[{"speaker": None, "source": str(i), "translation": str(i)} for i in range(12)],
        )
    )
    assert (src, tr, cached, usage.used, usage.limit) == ("I was", "Я был", False, 3, 50)
    assert seen["auth"] == "Bearer tok"
    body = seen["body"]
    assert body["game"] == {"title": "Midnight Station", "exe": "ms.exe"}
    assert len(body["context"]) == 8 and body["context"][-1]["source"] == "11"  # последние 8


def test_limit_reached_is_not_retryable_but_overload_is():
    def limit(req):
        return httpx.Response(
            429, json={"detail": {"code": "limit_reached", "message": "Лимит", "used": 50, "limit": 50}}
        )

    with pytest.raises(LexoError) as e:
        run(client_with(limit).me())
    assert e.value.code == "limit_reached" and e.value.extra["used"] == 50 and not e.value.retryable

    def busy(req):
        return httpx.Response(503, json={"detail": {"code": "ai_unavailable", "message": "Перевод недоступен."}})

    with pytest.raises(LexoError) as e2:
        run(client_with(busy).me())
    assert e2.value.retryable


def test_plain_401_and_html_errors_still_have_a_message():
    with pytest.raises(LexoError) as e:
        run(client_with(lambda r: httpx.Response(401, text="nope")).me())
    assert e.value.unauthorized and e.value.message

    with pytest.raises(LexoError) as e2:
        run(client_with(lambda r: httpx.Response(502, text="<html>bad gateway</html>")).me())
    assert e2.value.status == 502 and "502" in e2.value.message


def test_network_errors_become_offline():
    def down(req):
        raise httpx.ConnectError("no route")

    with pytest.raises(LexoOffline):
        run(client_with(down).me())

    def slow(req):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(LexoOffline, match="timeout"):
        run(client_with(slow).me())


def test_requests_without_token_fail_fast():
    called = []
    c = client_with(lambda r: called.append(1) or httpx.Response(200, json={}), token=None)
    with pytest.raises(LexoError) as e:
        run(c.me())
    assert e.value.status == 401 and not called


def test_unlink_forgets_token_even_on_error():
    c = client_with(lambda r: httpx.Response(500, json={}))
    with pytest.raises(LexoError):
        run(c.unlink())
    assert c.token is None


def test_lookup_and_card_translation():
    def handler(req):
        return httpx.Response(
            200,
            json={
                "lemma": "eerie",
                "pos": "adj",
                "ipa": "ˈɪəri",
                "senses": ["жуткий", "зловещий", "странный"],
                "marked_translation": "здесь **жутко**",
                "in_deck": True,
                "card_id": 7,
            },
        )

    sense = run(
        client_with(handler).lookup(
            session_id="s" * 36, game=GameRef("G"), selection="eerie", sentence="It is eerie.", sentence_translation=None
        )
    )
    assert sense.lemma == "eerie" and sense.in_deck and sense.card_id == 7
    assert sense.translation == "жуткий, зловещий"


def test_vocab_since_param():
    seen = {}

    def handler(req):
        seen["since"] = req.url.params.get("since")
        return httpx.Response(200, json={"items": ["eerie", "make amends"], "server_time": "2026-10-19T10:00:00Z"})

    items, t = run(client_with(handler).vocab("2026-10-18T00:00:00Z"))
    assert items == ["eerie", "make amends"] and t.startswith("2026-10-19")
    assert seen["since"] == "2026-10-18T00:00:00Z"


def test_lexo_translator_maps_errors_and_reports_usage():
    usages = []

    def ok(req):
        return httpx.Response(
            200,
            json={"source": "Hi", "translation": "Привет — друг", "cached": True, "usage": {"used": 1, "limit": 5}},
        )

    tr = LexoTranslator(client_with(ok), on_usage=usages.append)
    res = run(tr.translate(TranslateRequest("Hi", None, "G", line_id="l" * 36, session_id="s" * 36)))
    assert res.translation == "Привет, друг"  # без длинного тире
    assert usages and usages[0].used == 1

    def limit(req):
        return httpx.Response(429, json={"detail": {"code": "limit_reached", "message": "Лимит закончился"}})

    with pytest.raises(TranslationError) as e:
        run(LexoTranslator(client_with(limit)).translate(TranslateRequest("Hi", None, "G")))
    assert e.value.code == "limit_reached" and str(e.value) == "Лимит закончился"

    def down(req):
        raise httpx.ConnectError("x")

    with pytest.raises(TranslationError) as e2:
        run(LexoTranslator(client_with(down)).translate(TranslateRequest("Hi", None, "G")))
    assert e2.value.code == "offline" and "Нет связи" in str(e2.value)


def test_old_server_without_desktop_endpoints():
    with pytest.raises(LexoError) as e:
        run(client_with(lambda r: httpx.Response(404, json={"detail": "Not Found"}), None).link("ABCDEF", "pc"))
    assert e.value.code == "server_outdated" and "Обнови сервер" in e.value.message


def test_decks_list_and_create():
    def handler(req):
        if req.method == "POST":
            return httpx.Response(200, json={"id": 9, "title": json.loads(req.content)["title"], "card_count": 0})
        return httpx.Response(
            200,
            json={
                "items": [{"id": 3, "title": "Danganronpa", "card_count": 12, "due_count": 4, "new_count": 2}],
                "current_deck_id": 3,
            },
        )

    c = client_with(handler)
    decks, current = run(c.decks())
    assert decks[0].title == "Danganronpa" and decks[0].due_count == 4 and current == 3
    made = run(c.create_deck("  Слова из игр  "))
    assert made.id == 9 and made.title == "Слова из игр"
