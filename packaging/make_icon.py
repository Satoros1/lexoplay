"""Иконка exe: тот же логотип, что рисует трей (тёмно-бирюзовый квадрат с «L»)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def logo(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=int(size * 0.22), fill=(15, 110, 86, 255))
    font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "segoeuib.ttf"
    try:
        font = ImageFont.truetype(str(font_path), int(size * 0.62))
    except OSError:
        font = ImageFont.load_default()
    d.text((size / 2, size / 2), "L", fill=(255, 255, 255, 255), font=font, anchor="mm")
    return img


def main(out: str) -> None:
    big = logo(256)
    big.save(out, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    print("icon:", out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "lexoplay.ico")
