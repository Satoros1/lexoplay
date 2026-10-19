"""Режим отладки: кадр, картинка для OCR и распознанный текст в %APPDATA%\\LexoPlay\\debug.

Без этого OCR на конкретной новелле не настроить: видно, что именно ушло в распознавание.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from PIL import Image

log = logging.getLogger(__name__)


class DebugDump:
    def __init__(self, folder: Path, keep: int = 60) -> None:
        self._dir = folder
        self._keep = keep
        self._prefix = ""

    def new_shot(self) -> None:
        self._prefix = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"

    def __call__(self, name: str, payload: object) -> None:
        try:
            if not self._prefix:
                self.new_shot()
            if isinstance(payload, Image.Image):
                payload.save(self._dir / f"{self._prefix}-{name}.png")
            else:
                (self._dir / f"{self._prefix}-{name}.json").write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
                )
            self._cleanup()
        except Exception:
            log.exception("debug dump failed")

    def _cleanup(self) -> None:
        files = sorted(self._dir.glob("*"), key=lambda p: p.stat().st_mtime)
        for p in files[: max(0, len(files) - self._keep)]:
            p.unlink(missing_ok=True)
