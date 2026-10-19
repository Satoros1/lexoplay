"""Лог в %APPDATA%\\LexoPlay\\lexoplay.log: без него баги на чужом ПК не поймать."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from lexoplay.config import data_dir


def setup_logging(debug: bool = False) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = RotatingFileHandler(data_dir() / "lexoplay.log", maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if sys.stderr is not None:  # у windowed-exe stderr нет
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)

    def _hook(exc_type, exc, tb):  # type: ignore[no-untyped-def]
        logging.getLogger("lexoplay").critical("необработанная ошибка", exc_info=(exc_type, exc, tb))

    sys.excepthook = _hook
