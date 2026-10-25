"""Снимки для README: настоящие виджеты LexoPlay поверх нарисованных сцен.

Сцены рисуются здесь же (ночной город, окно диалога, страница текста): никаких кадров из чужих
игр. Запускается в CI на Windows, чтобы шрифты были как у игрока (Segoe UI), и кладёт PNG в
папку из аргумента. Локально тоже работает (QT_QPA_PLATFORM=offscreen), шрифты будут другие.

    python packaging/readme_shots.py out_dir
"""

from __future__ import annotations

import os
import random
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

W, H = 1280, 720
NAME = "Mira"
LINE = "I was reluctant to come here tonight, you know. This place feels eerie after dark."
LINE_RU = "Знаешь, мне совсем не хотелось сюда сегодня приходить. После заката здесь как-то жутко."
GAME = "Midnight Station"


def _font(size: int, bold: bool = False):  # type: ignore[no-untyped-def]
    from PIL import ImageFont

    win = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    names = ["segoeuib.ttf", "arialbd.ttf"] if bold else ["segoeui.ttf", "arial.ttf"]
    linux = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for path in [*(win / n for n in names), *(Path(p) for p in linux)]:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def night_scene(seed: int = 7):  # type: ignore[no-untyped-def]
    """Ночная станция: градиент неба, луна, силуэты домов с окнами, огни. Без персонажей."""
    from PIL import Image, ImageDraw, ImageFilter

    rnd = random.Random(seed)
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):  # небо: от тёмно-синего к фиолетовому у горизонта
        t = y / H
        d.line([(0, y), (W, y)], fill=(int(14 + 52 * t), int(18 + 20 * t), int(42 + 48 * t)))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    g.ellipse((900, 70, 1060, 230), fill=(255, 236, 200, 90))
    glow = glow.filter(ImageFilter.GaussianBlur(40))
    img.paste(glow, (0, 0), glow)
    d.ellipse((950, 120, 1010, 180), fill=(250, 240, 220))
    for _ in range(90):  # звёзды
        x, y = rnd.randrange(W), rnd.randrange(int(H * 0.45))
        v = rnd.randrange(150, 255)
        d.point((x, y), fill=(v, v, v))
    base = int(H * 0.66)
    x = 0
    while x < W:  # дома
        bw, bh = rnd.randrange(60, 150), rnd.randrange(90, 300)
        shade = rnd.randrange(14, 28)
        d.rectangle((x, base - bh, x + bw, H), fill=(shade, shade + 2, shade + 10))
        for wy in range(base - bh + 14, base - 10, 22):
            for wx in range(x + 10, x + bw - 14, 18):
                if rnd.random() < 0.28:
                    d.rectangle((wx, wy, wx + 8, wy + 11), fill=(250, 214, 140) if rnd.random() < 0.8 else (170, 210, 255))
        x += bw + rnd.randrange(0, 12)
    lights = Image.new("RGBA", (W, H), (0, 0, 0, 0))  # фонари платформы
    lg = ImageDraw.Draw(lights)
    for i in range(7):
        cx = 80 + i * 190
        lg.ellipse((cx - 30, base - 40, cx + 30, base + 20), fill=(255, 190, 120, 120))
    lights = lights.filter(ImageFilter.GaussianBlur(14))
    img.paste(lights, (0, 0), lights)
    d.rectangle((0, base + 18, W, H), fill=(22, 22, 30))
    for i in range(0, W, 64):
        d.line([(i, base + 40), (i + 40, base + 40)], fill=(70, 70, 84), width=3)
    return img


def dialog_scene():  # type: ignore[no-untyped-def]
    """Сцена с окном диалога внизу, как в визуальных новеллах."""
    from PIL import Image, ImageDraw

    img = night_scene().convert("RGBA")
    box = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(box)
    d.rounded_rectangle((48, 520, W - 48, 690), 18, fill=(12, 14, 24, 215), outline=(255, 255, 255, 60), width=2)
    d.rounded_rectangle((72, 496, 220, 540), 12, fill=(120, 96, 220, 255))
    img = Image.alpha_composite(img, box)
    d = ImageDraw.Draw(img)
    d.text((96, 503), NAME, font=_font(26, bold=True), fill=(255, 255, 255))
    f = _font(30)
    d.text((88, 566), "I was reluctant to come here tonight, you know.", font=f, fill=(244, 242, 236))
    d.text((88, 612), "This place feels eerie after dark.", font=f, fill=(244, 242, 236))
    return img


PAGE = [
    "The last train had left an hour ago, and the station was empty except for the hum of the lights.",
    '"You came after all," Mira said, without looking up from the timetable.',
    "I wanted to tell her that I almost didn't. That the closer I got, the more the place felt wrong.",
    "Somewhere down the tracks, a bell began to ring.",
]
PAGE_RU = [
    "Последний поезд ушёл час назад, и на станции было пусто, только гудели лампы.",
    "«Всё-таки пришёл», сказала Мира, не отрываясь от расписания.",
    "Я хотел сказать, что чуть не передумал: чем ближе подходил, тем сильнее казалось, что тут что-то не так.",
    "Где-то дальше по путям зазвонил колокол.",
]


def page_scene():  # type: ignore[no-untyped-def]
    """Текст на весь экран (NVL): абзацы поверх затемнённой сцены. Возвращает кадр и рамки абзацев."""
    import textwrap

    from PIL import Image, ImageDraw

    img = night_scene(11).convert("RGBA")
    veil = Image.new("RGBA", (W, H), (6, 8, 16, 185))
    img = Image.alpha_composite(img, veil)
    d = ImageDraw.Draw(img)
    f = _font(28)
    y = 80
    rects = []
    for para in PAGE:
        top = y
        right = 0
        for line in textwrap.wrap(para, 62):
            d.text((110, y), line, font=f, fill=(240, 238, 230))
            right = max(right, d.textbbox((110, y), line, font=f)[2])
            y += 40
        rects.append((110, top, right - 110, y - top - 6))
        y += 34
    return img, rects


def compose(scene, widget, path: Path) -> None:  # type: ignore[no-untyped-def]
    """Виджет LexoPlay (прозрачное окно) поверх сцены."""
    from PIL import Image
    from PySide6.QtCore import QBuffer, QIODevice

    img = widget.grab().toImage()
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    import io

    over = Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA").resize((W, H))
    Image.alpha_composite(scene.convert("RGBA"), over).convert("RGB").save(path, optimize=True)


def pump(app, n: int = 8) -> None:  # type: ignore[no-untyped-def]
    for _ in range(n):
        app.processEvents()
        time.sleep(0.02)


def main(out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv[:1])
    from lexoplay.geometry import Rect
    from lexoplay.recall import RecallChip
    from lexoplay.ui import theme
    from lexoplay.ui.overlay import Overlay, PageView
    from lexoplay.ui.word_layer import WordLayer
    from lexoplay.wordmode import CardState, ExplainRow, ExplainState, reader_views

    family = theme.load_fonts()
    region = Rect(48 / W, 490 / H, (W - 96) / W, 200 / H)  # окно диалога вместе с плашкой имени
    display = Rect(0, 0, W, H)
    scene = dialog_scene()
    scene.convert("RGB").save(out / "scene-dialog.png", optimize=True)

    # 1. перевод над окном диалога и подсказка о сохранённом слове
    ov = Overlay()
    ov.fade = False
    ov.font_scale = 1.5  # на снимке 1280×720 плашка как на большом мониторе
    ov.set_display_rect(display)
    ov.resize(W, H)
    ov.show_text(region, LINE_RU, recall=[RecallChip("eerie", "жуткий", 3)])
    ov.show()
    pump(app)
    compose(scene, ov, out / "translate.png")
    ov.hide_strip()

    # 2. режим слов: карточка слова
    known = {"reluctant"}
    views = reader_views(LINE, lambda w: w.strip(".,").lower() in known, level="b2", names={NAME})
    layer = WordLayer()
    layer.animate = False
    layer.font_scale = 1.2
    layer.rare_label = "B2+"
    layer.deck_label = f"Колода «{GAME}»"
    layer.can_retranslate = True
    layer.resize(W, H)
    layer.open(display, region, views, speaker=NAME, translation=LINE_RU)
    pump(app)
    eerie = next(v.index for v in views if v.text.startswith("eerie"))
    layer.select([eerie])
    layer.set_card(CardState("ready", "eerie", "ˈɪəri", "adj", ["жуткий", "зловещий", "странный, пугающий"]))
    layer.show()
    pump(app)
    compose(scene, layer, out / "words.png")

    # 3. разбор реплики (F7)
    idx = {v.text.strip(".,").lower(): v.index for v in views}
    layer.set_explain(
        ExplainState(
            "ready",
            rows=[
                ExplainRow("reluctant to", "без желания, нехотя", "be reluctant to do: «не хотеть что-то делать»", [idx["reluctant"], idx["to"]]),
                ExplainRow("feels eerie", "кажется жутким", "feel + прилагательное: «ощущается каким-то»", [idx["feels"], idx["eerie"]], base="feel eerie"),
                ExplainRow("after dark", "после заката", "устойчивое выражение", [idx["after"], idx["dark"]]),
            ],
            summary="Мира признаётся, что шла сюда нехотя: днём место обычное, а ночью от него не по себе.",
            literal="Я была неохотна прийти сюда сегодня вечером, знаешь. Это место ощущается жутким после темноты.",
        )
    )
    pump(app)
    compose(scene, layer, out / "explain.png")
    layer.finish("closed")
    layer.close()

    # 4. текст на весь экран: перевод поверх абзацев
    page, rects = page_scene()
    page.convert("RGB").save(out / "scene-page.png", optimize=True)
    ov.show_page(
        [
            PageView(Rect(x / W, y / H, w / W, h / H), 30 / H, text)
            for (x, y, w, h), text in zip(rects, PAGE_RU, strict=True)
        ]
    )
    pump(app)
    compose(page, ov, out / "page.png")
    ov.hide_page()
    ov.close()

    # 5. окно LexoPlay: игры, история, слова, прогресс
    windows(app, out)
    print("readme shots:", family, sorted(p.name for p in out.glob("*.png")))
    return 0


def windows(app, out: Path) -> None:  # type: ignore[no-untyped-def]
    from datetime import date, timedelta

    from lexoplay.config import Settings
    from lexoplay.geometry import Rect
    from lexoplay.lexo import Deck
    from lexoplay.store import Game, SavedWord, Store
    from lexoplay.ui.main_window import AccountStatus, MainWindow, UpdateState

    tmp = Path(tempfile.mkdtemp(prefix="lexoplay-readme-"))
    store = Store(tmp / "local.db")
    store.save_game(Game("midnight.exe", GAME, region=Rect(0.04, 0.72, 0.92, 0.24), name_region=Rect(0.05, 0.68, 0.14, 0.06)))
    store.save_game(Game("lantern.exe", "Lantern Hill", region=Rect(0.05, 0.05, 0.9, 0.88), text_mode="page"))
    now = time.time()
    lines = [
        ("Mira", "I was reluctant to come here tonight, you know.", "Знаешь, мне совсем не хотелось сюда сегодня приходить."),
        ("Mira", "This place feels eerie after dark.", "После заката здесь как-то жутко."),
        ("Ren", "Don't jump to conclusions. Look at the timetable first.", "Не спеши с выводами. Сначала посмотри на расписание."),
        ("Ren", "The last train never left. It's still here, somewhere.", "Последний поезд так и не ушёл. Он всё ещё где-то здесь."),
        ("Mira", "If you want to make amends, help me find it.", "Хочешь загладить вину? Помоги мне его найти."),
        (None, "A bell began to ring somewhere down the tracks.", "Где-то дальше по путям зазвонил колокол."),
    ]
    for i, (sp, src, tr) in enumerate(lines):
        store.line_add("midnight.exe", "ru", f"l{i}", sp, src, tr)
        with store._lock:  # noqa: SLF001
            store._db.execute("UPDATE lines SET created_at = ?, seen_at = ? WHERE norm = ?", (now - 600 + i * 40, now - 600 + i * 40, f"l{i}"))  # noqa: SLF001
            store._db.commit()  # noqa: SLF001
    store.line_star(store.lines_list(query="amends")[0].id, True)
    words = [
        ("eerie", "eerie", "жуткий, зловещий", "ˈɪəri", "adj", "This place feels eerie after dark.", "После заката здесь как-то жутко.", 3),
        ("reluctant", "reluctant", "неохотный, нежелающий", "rɪˈlʌktənt", "adj", "I was reluctant to come here tonight, you know.", "Знаешь, мне совсем не хотелось сюда сегодня приходить.", 1),
        ("make amends", "make amends", "загладить вину", "", "phrase", "If you want to make amends, help me find it.", "Хочешь загладить вину? Помоги мне его найти.", 0),
        ("jump to conclusions", "jump to conclusions", "делать поспешные выводы", "", "phrase", "Don't jump to conclusions. Look at the timetable first.", "Не спеши с выводами. Сначала посмотри на расписание.", 2),
        ("timetable", "timetable", "расписание", "ˈtaɪmˌteɪbəl", "noun", "Don't jump to conclusions. Look at the timetable first.", "Не спеши с выводами. Сначала посмотри на расписание.", 0),
    ]
    for k, (sel, lemma, tr, ipa, pos, sent, sent_tr, seen) in enumerate(words):
        wid = store.word_add(SavedWord("midnight.exe", GAME, sel, lemma, tr, ipa, pos, sent, sent_tr, created_at=now - 3000 + k * 300))
        if seen and isinstance(wid, int):
            store.words_seen([wid] * 1, now)
    for back, (n_lines, n_words, secs) in enumerate([(64, 9, 4200), (38, 5, 2700), (0, 0, 0), (51, 7, 3600), (72, 12, 5100), (27, 3, 1800), (44, 6, 3000)]):
        day = (date.today() - timedelta(days=back)).isoformat()
        store.stats_add(day, "midnight.exe", lines=n_lines, words=n_words, seconds=secs)

    class Ctl:
        settings = Settings()

        def __init__(self) -> None:
            self.store = store
            self.update = UpdateState()

        def account_status(self):  # type: ignore[no-untyped-def]
            return AccountStatus(True, "@player", used=12, limit=50, queue=0)

        def load_decks(self, done):  # type: ignore[no-untyped-def]
            done([Deck(3, GAME, 41, 6, 3), Deck(5, "Слова из игр", 120, 18, 0)], 3, None)

        def active_exe(self):  # type: ignore[no-untyped-def]
            return "midnight.exe"

        def api_key(self):  # type: ignore[no-untyped-def]
            return ""

        def usage_summary(self):  # type: ignore[no-untyped-def]
            return "Сегодня реплик: 64, слов: 9, кеш промпта: 71% входа. Это примерно $0.03."

        def history(self, exe=None, query="", limit=100, offset=0):  # type: ignore[no-untyped-def]
            return store.lines_list(exe, query, limit, offset)

        def saved_words(self):  # type: ignore[no-untyped-def]
            return store.words_list()

        def speakers(self, exe):  # type: ignore[no-untyped-def]
            return store.line_speakers(exe)

        def glossary(self, exe):  # type: ignore[no-untyped-def]
            return store.glossary(exe)

        def get_last_read(self, exe):  # type: ignore[no-untyped-def]
            if exe != "midnight.exe":
                return None
            shot = dialog_scene().convert("RGB").crop((48, 520, W - 48, 690))
            return SimpleNamespace(image=shot, text=LINE, mode_name="обычный")

        def autostart_supported(self):  # type: ignore[no-untyped-def]
            return True

        def autostart_enabled(self):  # type: ignore[no-untyped-def]
            return True

        def progress(self):  # type: ignore[no-untyped-def]
            from lexoplay.progress import progress

            return progress(store, 10)

        def __getattr__(self, _name):  # type: ignore[no-untyped-def]
            return lambda *a, **k: None

    from PIL import Image

    w = MainWindow(Ctl())
    w.resize(1100, 720)
    w.show()
    for key in ("games", "history", "words", "progress"):
        w.show_page(key)
        pump(app, 10)
        if key == "history":
            pump(app, 20)
            cards = list(getattr(w.history, "cards", []) or [])
            print("history cards:", len(cards))
            if cards and not cards[0].opened:
                cards[0].set_open(True)  # первую сессию раскрыть: видно реплики и переводы
                cards[0].flush()
                pump(app, 30)
            print("history opened:", [c.opened for c in getattr(w.history, "cards", [])], cards[0] is w.history.cards[0] if cards else None)
        img = w.grab().toImage()
        path = out / f"window-{key}.png"
        img.save(str(path))
        Image.open(path).convert("RGB").save(path, optimize=True)
    w.hide()
    store.close()


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "readme-shots")))
