"""Клиент сервера Lexo: эндпоинты /desktop/* по токену устройства.

Ключа Anthropic у клиента нет: реплики и слова переводит сервер, с кешем, дневным
лимитом и языками игрока из его профиля в Lexo. Ошибки сервера приходят как
{"detail": {"code", "message", ...}}: message можно показывать игроку как есть.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from lexoplay import __version__

log = logging.getLogger(__name__)

DEFAULT_SERVER = "https://api.lexovium.study"


class LexoError(Exception):
    """Сервер ответил ошибкой. code: bad_code, limit_reached, device_revoked, update_required…"""

    def __init__(self, status: int, code: str, message: str, extra: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra or {}

    @property
    def unauthorized(self) -> bool:
        return self.status == 401

    @property
    def retryable(self) -> bool:
        """Повторять имеет смысл: сервер перегружен или упёрся в частоту, а не отказал по сути."""
        return self.status >= 500 or (self.status in (408, 429) and self.code != "limit_reached")


class LexoOffline(Exception):
    """До сервера не достучались: нет сети, DNS, таймаут."""


@dataclass(frozen=True, slots=True)
class Account:
    username: str | None
    native_lang: str
    learning_lang: str
    premium: bool

    @property
    def label(self) -> str:
        return f"@{self.username}" if self.username else "аккаунт Lexo"

    @staticmethod
    def from_json(d: dict[str, Any]) -> Account:
        return Account(
            username=d.get("username"),
            native_lang=str(d.get("native_lang") or "ru"),
            learning_lang=str(d.get("learning_lang") or "en"),
            premium=bool(d.get("premium")),
        )


@dataclass(frozen=True, slots=True)
class Usage:
    used: int
    limit: int

    @staticmethod
    def from_json(d: dict[str, Any] | None) -> Usage | None:
        if not d:
            return None
        return Usage(int(d.get("used", 0)), int(d.get("limit", 0)))


@dataclass(frozen=True, slots=True)
class Sense:
    lemma: str
    pos: str
    ipa: str
    senses: list[str] = field(default_factory=list)
    marked_translation: str = ""
    in_deck: bool = False
    card_id: int | None = None

    @property
    def translation(self) -> str:
        """Что уходит в поле перевода карточки: два первых значения, как в Mini App."""
        return ", ".join(self.senses[:2])


@dataclass(frozen=True, slots=True)
class Deck:
    id: int
    title: str
    card_count: int = 0
    due_count: int = 0
    new_count: int = 0

    @staticmethod
    def from_json(d: dict[str, Any]) -> Deck:
        return Deck(
            int(d["id"]),
            str(d.get("title") or ""),
            int(d.get("card_count") or 0),
            int(d.get("due_count") or 0),
            int(d.get("new_count") or 0),
        )


@dataclass(frozen=True, slots=True)
class GameRef:
    title: str
    exe: str = ""

    def to_json(self) -> dict[str, str]:
        return {"title": self.title[:128] or "Игра", "exe": self.exe[:128]}


def _error_from(resp: httpx.Response) -> LexoError:
    code, message, extra = "http_error", f"Сервер Lexo ответил ошибкой {resp.status_code}.", {}
    try:
        detail = resp.json().get("detail")
    except Exception:
        detail = None
    if isinstance(detail, dict):
        code = str(detail.get("code") or code)
        message = str(detail.get("message") or message)
        extra = {k: v for k, v in detail.items() if k not in ("code", "message")}
    elif isinstance(detail, str) and detail:
        message = detail
    if resp.status_code == 401 and code == "http_error":
        code, message = "unauthorized", "ПК не привязан к Lexo."
    if resp.status_code in (404, 405) and code == "http_error":
        # путь /desktop/* не найден: сервер Lexo ещё без LexoPlay или адрес сервера не тот
        code = "server_outdated"
        message = "На сервере Lexo ещё нет LexoPlay. Обнови сервер или проверь адрес во вкладке «Дополнительно»."
    return LexoError(resp.status_code, code, message, extra)


class LexoClient:
    """Асинхронный. Живёт в BackgroundLoop: httpx-клиент создаётся в том же цикле, где им пользуются."""

    def __init__(
        self,
        server: str,
        token: str | None = None,
        *,
        timeout: float = 8.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.server = (server or DEFAULT_SERVER).rstrip("/")
        self.token = token
        self._timeout = timeout
        self._transport = transport
        self._http: httpx.AsyncClient | None = None

    @property
    def linked(self) -> bool:
        return bool(self.token)

    def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=self.server,
                timeout=self._timeout,
                transport=self._transport,
                headers={"X-LexoPlay-Version": __version__, "User-Agent": f"LexoPlay/{__version__}"},
            )
        return self._http

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        auth: bool = True,
        timeout: float | None = None,
    ) -> Any:
        headers = {}
        if auth:
            if not self.token:
                raise LexoError(401, "unauthorized", "ПК не привязан к Lexo.")
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            resp = await self._client().request(
                method,
                path,
                json=json,
                params=params,
                headers=headers,
                timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
            )
        except httpx.TimeoutException as exc:
            raise LexoOffline("timeout") from exc
        except httpx.HTTPError as exc:
            raise LexoOffline(str(exc) or type(exc).__name__) from exc
        if resp.status_code >= 400:
            err = _error_from(resp)
            log.info("Lexo %s %s -> %s %s", method, path, resp.status_code, err.code)
            raise err
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    # ------------------------------------------------------------------ аккаунт
    async def link(self, code: str, device_name: str) -> tuple[str, Account]:
        data = await self._request(
            "POST",
            "/desktop/link",
            json={"code": code.strip().upper(), "device_name": device_name[:64], "client_version": __version__},
            auth=False,
        )
        self.token = data["token"]
        return data["token"], Account.from_json(data["user"])

    async def me(self) -> tuple[Account, Usage | None, str]:
        data = await self._request("GET", "/desktop/me")
        return Account.from_json(data["user"]), Usage.from_json(data.get("usage")), str(data.get("device_name", ""))

    async def unlink(self) -> None:
        try:
            await self._request("DELETE", "/desktop/device")
        finally:
            self.token = None

    async def decks(self) -> tuple[list[Deck], int | None]:
        data = await self._request("GET", "/desktop/decks")
        return [Deck.from_json(d) for d in data.get("items", [])], data.get("current_deck_id")

    async def create_deck(self, title: str) -> Deck:
        return Deck.from_json(await self._request("POST", "/desktop/decks", json={"title": title.strip()[:128]}))

    async def vocab(self, since: str | None = None) -> tuple[list[str], str, dict[str, str]]:
        """(словарные формы, время сервера, перевод по слову). Перевод отдаёт сервер с 0.6.27, старый: {}."""
        data = await self._request("GET", "/desktop/vocab", params={"since": since} if since else None, timeout=20)
        translations = {
            str(e.get("text") or "").strip().lower(): str(e.get("translation") or "").strip()
            for e in data.get("entries") or []
            if isinstance(e, dict) and e.get("text")
        }
        return [str(x) for x in data.get("items", [])], str(data.get("server_time", "")), translations

    # ------------------------------------------------------------------ игра
    async def translate(
        self,
        *,
        session_id: str,
        line_id: str,
        game: GameRef,
        text: str,
        speaker: str | None,
        context: list[dict[str, Any]],
        glossary: dict[str, str] | None = None,
        previous: str | None = None,
    ) -> tuple[str, str, bool, Usage | None]:
        body: dict[str, Any] = {
            "session_id": session_id,
            "line_id": line_id,
            "game": game.to_json(),
            "speaker": (speaker or None) and speaker[:64],
            "text": text[:2000],
            "context": context[-12:],
        }
        if glossary:
            body["glossary"] = {k[:64]: v[:128] for k, v in list(glossary.items())[:50]}
        if previous:
            body["previous"] = previous[:2000]  # «Перевести иначе»: сервер не возьмёт перевод из кеша
        data = await self._request("POST", "/desktop/translate", json=body)
        return data["source"], data["translation"], bool(data.get("cached")), Usage.from_json(data.get("usage"))

    async def lookup(
        self,
        *,
        session_id: str,
        game: GameRef,
        selection: str,
        sentence: str,
        sentence_translation: str | None,
    ) -> Sense:
        data = await self._request(
            "POST",
            "/desktop/lookup",
            json={
                "session_id": session_id,
                "game": game.to_json(),
                "selection": selection[:80],
                "sentence": sentence[:2000],
                "sentence_translation": sentence_translation,
            },
            timeout=12,
        )
        return Sense(
            lemma=str(data.get("lemma") or selection),
            pos=str(data.get("pos") or ""),
            ipa=str(data.get("ipa") or ""),
            senses=[str(s) for s in data.get("senses", []) if str(s).strip()],
            marked_translation=str(data.get("marked_translation") or ""),
            in_deck=bool(data.get("in_deck")),
            card_id=data.get("card_id"),
        )

    async def explain(
        self,
        *,
        session_id: str,
        game: GameRef,
        sentence: str,
        speaker: str | None,
        translation: str | None,
        context: list[dict[str, Any]],
    ):  # type: ignore[no-untyped-def]
        """Разбор реплики: сленг, идиомы, трудные места. -> lookup.Explanation"""
        from lexoplay.lookup import ExplainItem, Explanation

        data = await self._request(
            "POST",
            "/desktop/explain",
            json={
                "session_id": session_id,
                "game": game.to_json(),
                "sentence": sentence[:2000],
                "speaker": (speaker or None) and speaker[:64],
                "translation": (translation or None) and translation[:2000],
                "context": context[-6:],
            },
            timeout=25,
        )
        items = tuple(
            ExplainItem(
                str(i.get("phrase") or ""), str(i.get("meaning") or ""), str(i.get("note") or ""), str(i.get("base") or "")
            )
            for i in data.get("items", [])
            if i.get("phrase") and i.get("meaning")
        )
        # старый сервер шлёт только summary, новый ещё literal и base у пунктов
        return Explanation(items, str(data.get("summary") or ""), str(data.get("literal") or ""))

    async def add_card(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/desktop/cards", json=payload, timeout=20)

    async def end_session(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", f"/desktop/sessions/{session_id}/end", json=payload)
