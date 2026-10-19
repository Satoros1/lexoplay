"""Иконка exe: тот же логотип, что в окне и трее (облачко реплики на зелёном квадрате).

Рисуется тем же кодом Qt (lexoplay.ui.theme.draw_logo), каждый размер отдельно:
у маленьких свои упрощения, поэтому не уменьшаем одну большую картинку.
"""

from __future__ import annotations

import io
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image  # noqa: E402
from PySide6.QtCore import QBuffer, QIODevice  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def render(size: int) -> Image.Image:
    from lexoplay.ui.theme import draw_logo

    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    draw_logo(size).save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA")


def main(out: str) -> None:
    _app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    images = [render(s) for s in SIZES]
    big = images[-1]
    big.save(out, format="ICO", sizes=[(s, s) for s in SIZES], append_images=images[:-1])
    print("icon:", out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "lexoplay.ico")
