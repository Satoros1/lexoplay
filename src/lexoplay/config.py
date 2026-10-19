"""Настройки (%APPDATA%\\LexoPlay\\settings.json) и ключ Anthropic (Windows Credential Manager)."""

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
    translate: str = "F2"
    words: str = "F3"
    hide: str = "F4"
    reselect: str = "Ctrl+F2"
    add_game: str = "Ctrl+Shift+F2"  # глобальная: работает в любом окне


@dataclass
class Settings:
    model: str = "claude-haiku-5-5"
    target_lang: str = "ru"
    source_lang: str = "en"
    magpie_mode: str = "auto"  # auto | always | never
    font_scale: float = 1.0
    strip_opacity: float = 0.84
    context_lines: int = 8
    debug: bool = False
    hotkeys: Hotkeys = field(default_factory=Hotkeys)

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
        data = {k: v for k, v in raw.items() if k in known and k != "hotkeys"}
        hk_known = {f.name for f in fields(Hotkeys)}
        hk = Hotkeys(**{k: v for k, v in (raw.get("hotkeys") or {}).items() if k in hk_known})
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


def get_api_key() -> str | None:
    env = os.environ.get("ANTHROPIC_API_KEY")
    if env:
        return env
    try:
        keyring = _keyring()

        return keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
    except Exception:
        log.exception("keyring недоступен")
        return None


def set_api_key(value: str) -> bool:
    try:
        keyring = _keyring()

        if value:
            keyring.set_password(KEYRING_SERVICE, KEYRING_USER, value)
        else:
            try:
                keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
            except Exception:
                pass
        return True
    except Exception:
        log.exception("не удалось сохранить ключ в keyring")
        return False
