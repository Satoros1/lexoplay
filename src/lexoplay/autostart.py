"""«Запускать вместе с Windows»: запись в HKCU\\...\\Run, без прав администратора.

При запуске из автозагрузки (--autostart) LexoPlay стартует тихо, сразу в трей.
Если exe перенесли в другую папку, путь в записи обновляется при следующем запуске.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "LexoPlay"


def supported() -> bool:
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def command(exe: Path | None = None) -> str:
    exe = exe or Path(sys.executable).resolve()
    return f'"{exe}" --autostart'


def _read() -> str | None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _type = winreg.QueryValueEx(key, VALUE)
            return str(value)
    except FileNotFoundError:
        return None


def enabled() -> bool:
    if sys.platform != "win32":
        return False
    try:
        return _read() is not None
    except OSError:
        log.exception("автозапуск: не прочитать реестр")
        return False


def set_enabled(on: bool) -> bool:
    """True, если получилось."""
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if on:
                winreg.SetValueEx(key, VALUE, 0, winreg.REG_SZ, command())
            else:
                try:
                    winreg.DeleteValue(key, VALUE)
                except FileNotFoundError:
                    pass
        log.info("автозапуск %s", "включён" if on else "выключен")
        return True
    except OSError:
        log.exception("автозапуск: не записать в реестр")
        return False


def refresh() -> None:
    """Exe перенесли или обновили под другим именем: поправить путь в автозапуске."""
    if not supported():
        return
    try:
        current = _read()
    except OSError:
        return
    if current is not None and current != command():
        set_enabled(True)
