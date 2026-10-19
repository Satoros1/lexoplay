"""«Собрать отчёт об ошибке»: zip с логом, настройками и списком игр для разработчика.

Секретов в отчёте нет: ключ Anthropic и токен Lexo лежат в Windows Credential Manager,
а не в settings.json. Отчёт кладётся на рабочий стол, Проводник сразу его показывает.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import zipfile
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from lexoplay import __version__
from lexoplay.config import Settings, data_dir, debug_dir
from lexoplay.store import Store


def _desktop() -> Path:
    for p in (Path.home() / "Desktop", Path.home() / "Рабочий стол", Path.home() / "OneDrive" / "Desktop"):
        if p.is_dir():
            return p
    return data_dir()


def _screens() -> list[dict[str, Any]]:
    try:
        from PySide6.QtGui import QGuiApplication

        return [
            {"name": s.name(), "geometry": s.geometry().getRect(), "dpr": s.devicePixelRatio()}
            for s in QGuiApplication.screens()
        ]
    except Exception:
        return []


def make_report(settings: Settings, store: Store, extra: dict[str, Any] | None = None, dest: Path | None = None) -> Path:
    folder = dest or _desktop()
    path = folder / f"LexoPlay-отчёт-{datetime.now():%Y%m%d-%H%M%S}.zip"
    info = {
        "version": __version__,
        "python": sys.version,
        "frozen": bool(getattr(sys, "frozen", False)),
        "platform": platform.platform(),
        "screens": _screens(),
        "created": datetime.now().isoformat(),
        **(extra or {}),
    }
    games = [
        {
            "exe": g.exe,
            "title": g.title,
            "region": g.region.to_dict() if g.region else None,
            "name_region": g.name_region.to_dict() if g.name_region else None,
            "capture": g.capture,
            "deck_mode": g.deck_mode,
            "deck_title": g.deck_title,
        }
        for g in store.list_games()
    ]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("info.json", json.dumps(info, ensure_ascii=False, indent=2, default=str))
        z.writestr("games.json", json.dumps(games, ensure_ascii=False, indent=2))
        z.writestr("settings.json", json.dumps(asdict(settings), ensure_ascii=False, indent=2))
        for log in sorted(data_dir().glob("lexoplay.log*")):
            z.write(log, f"logs/{log.name}")
        shots = sorted((p for p in debug_dir().rglob("*") if p.is_file()), key=lambda p: p.stat().st_mtime)[-40:]
        for shot in shots:
            z.write(shot, f"debug/{shot.relative_to(debug_dir())}")
    return path


def reveal(path: Path) -> None:
    """Показать файл в Проводнике (на Windows), чтобы его было легко прислать."""
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", str(path)])  # noqa: S603, S607
