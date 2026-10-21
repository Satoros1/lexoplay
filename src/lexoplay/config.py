"""Настройки (%APPDATA%\\LexoPlay\\settings.json) и секреты в Windows Credential Manager:
токен устройства Lexo и, для работы без Lexo, свой ключ Anthropic."""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from lexoplay import APP_NAME

log = logging.getLogger(__name__)

KEYRING_SERVICE = APP_NAME
KEYRING_USER = "anthropic_api_key"
KEYRING_TOKEN = "lexo_device_token"


def data_dir() -> Path:
    base = os.environ.get("LEXOPLAY_HOME")
    if base:
        path = Path(base)
    elif sys.platform == "win32":
        path = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
    else:
        path = Path.home() / ".lexoplay"
    path.mkdir(parents=True, exist_ok=True)
    return path


def debug_dir() -> Path:
    path = data_dir() / "debug"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Hotkeys:
    """Работают только пока на экране игра (кроме add_game). Без Ctrl и Shift: в новеллах
    они пропускают текст. add_game по умолчанию не задана: игра добавляется из окна LexoPlay."""

    translate: str = "F2"
    words: str = "F3"
    hide: str = "F4"
    reselect: str = "F6"
    explain: str = "F7"
    add_game: str = ""

    def items(self) -> list[tuple[str, str]]:
        return [(f.name, getattr(self, f.name)) for f in fields(self)]


HOTKEY_LABELS = {
    "translate": ("Перевести реплику", "Ещё раз спрячет перевод"),
    "words": ("Слова в Lexo", "Подчеркнуть слова реплики и добавить в карточки"),
    "hide": ("Спрятать перевод", ""),
    "reselect": ("Выделить окно диалога заново", "Если перевод встал не туда, и поставить плашку перевода, куда удобно"),
    "explain": ("Разбор реплики", "Сленг, идиомы и трудные места реплики с объяснением, их можно сохранить в слова"),
    "add_game": ("Добавить текущее окно как игру", "Работает в любой программе, поэтому по умолчанию выключено"),
}

SETTINGS_VERSION = 4


@dataclass
class Settings:
    server_url: str = "https://api.lexovium.study"
    deck: str = "game"  # game: колода с названием игры | current: текущая колода в Lexo
    model: str = "claude-haiku-5-5"
    better_model: str = "claude-sonnet-5-5"  # «Перевести иначе»: одна реплика моделью посильнее
    target_lang: str = "ru"
    source_lang: str = "en"
    magpie_mode: str = "auto"  # auto | always | never
    font_scale: float = 1.0
    strip_opacity: float = 0.84
    context_lines: int = 12  # сколько прошлых реплик видит модель: имена, пол, «ты/вы»
    debug: bool = False
    auto_translate: bool = False  # после пропуска реплики следующая переводится сама
    show_original: bool = False  # английский оригинал мелко над переводом
    check_updates: bool = True
    strip_place: str = "above"  # above | below | top: где плашка перевода, если для игры не задано своё место
    strip_font: str = ""  # шрифт перевода; пусто = как в окне LexoPlay (Segoe UI)
    strip_bold: bool = False
    daily_goal: int = 10  # цель на день: столько новых слов
    # режим слов: свернуть игру и показать на её месте снимок экрана, чтобы она точно не поймала клик.
    # auto: только у игр, которые ловят клики (Danganronpa и те, что поймали клик при открытом F3)
    words_freeze: str = "auto"  # auto | always | never
    freeze_games: list[str] = field(default_factory=list)
    hover_pick: bool = True  # режим слов: слово выбирается наведением, «+» удержанием курсора (без кликов)
    rare_level: str = "b2"  # режим слов: подсвечивать трудные слова с этого уровня (b1 | b2 | c1 | off)
    recall_words: bool = True  # под переводом напоминать сохранённые слова, которые снова встретились
    translate_think: bool = True  # свой ключ: модель немного думает перед переводом (точнее, на 1–2 с дольше)
    no_focus_games: list[str] = field(default_factory=list)  # свернулись, когда режим слов брал фокус: больше не берём
    hotkeys: Hotkeys = field(default_factory=Hotkeys)
    version: int = SETTINGS_VERSION

    @staticmethod
    def path() -> Path:
        return data_dir() / "settings.json"

    @classmethod
    def load(cls) -> Settings:
        p = cls.path()
        if not p.exists():
            return cls()
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.exception("settings.json повреждён, беру значения по умолчанию")
            return cls()
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in raw.items() if k in known and k not in ("hotkeys", "version")}
        hk_known = {f.name for f in fields(Hotkeys)}
        hk = Hotkeys(**{k: v for k, v in (raw.get("hotkeys") or {}).items() if k in hk_known})
        if int(raw.get("version") or 1) < 3:
            # 0.1–0.2: Ctrl+F2 и Ctrl+Shift+F2 пропускали текст в новеллах
            if hk.reselect.replace(" ", "").lower() == "ctrl+f2":
                hk.reselect = "F6"
            if hk.add_game.replace(" ", "").lower() == "ctrl+shift+f2":
                hk.add_game = ""
        if int(raw.get("version") or 1) < 4 and data.get("context_lines") == 8:
            data["context_lines"] = 12  # 0.4: больше контекста, перевод ровнее
        return cls(**data, hotkeys=hk)

    def save(self) -> None:
        self.path().write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")


def _keyring():  # type: ignore[no-untyped-def]
    """keyring ищет бэкенды через entry points, а в собранном exe их метаданных может не быть.
    На Windows поэтому ставим Credential Manager явно."""
    import keyring

    if sys.platform == "win32":
        try:
            from keyring.backends.Windows import WinVaultKeyring

            if not isinstance(keyring.get_keyring(), WinVaultKeyring):
                keyring.set_keyring(WinVaultKeyring())
        except Exception:
            log.exception("WinVaultKeyring недоступен")
    return keyring


def _get_secret(user: str, env: str) -> str | None:
    value = os.environ.get(env)
    if value:
        return value
    try:
        return _keyring().get_password(KEYRING_SERVICE, user)
    except Exception:
        log.exception("keyring недоступен")
        return None


def _set_secret(user: str, value: str | None) -> bool:
    try:
        keyring = _keyring()
        if value:
            keyring.set_password(KEYRING_SERVICE, user, value)
        else:
            try:
                keyring.delete_password(KEYRING_SERVICE, user)
            except Exception:
                pass
        return True
    except Exception:
        log.exception("не удалось сохранить секрет в keyring")
        return False


def get_token() -> str | None:
    return _get_secret(KEYRING_TOKEN, "LEXOPLAY_TOKEN")


def set_token(value: str | None) -> bool:
    return _set_secret(KEYRING_TOKEN, value)


def server_url(settings: Settings) -> str:
    return (os.environ.get("LEXOPLAY_SERVER") or settings.server_url).rstrip("/")


def get_api_key() -> str | None:
    return _get_secret(KEYRING_USER, "ANTHROPIC_API_KEY")


def set_api_key(value: str) -> bool:
    return _set_secret(KEYRING_USER, value)
