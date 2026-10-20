"""Самопроверка на настоящей Windows (её запускает CI после сборки exe).

Проверяет то, что нельзя проверить юнит-тестами на Linux: winrt OCR на картинке с текстом,
захват окна через PrintWindow и экрана, отрисовку оверлея, регистрацию хоткея, HTTPS до
сервера Lexo из собранного exe и весь путь слова (F3 → значение → «+ в Lexo» → outbox)
на настоящем App с поддельным сервером.
Статусы: ok, fail (роняет сборку), warn (CI-машина без нужного, например без OCR-пакета), skip.
Рядом с отчётом кладёт картинки, чтобы было видно, как выглядит строка перевода.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import traceback
from collections.abc import Callable
from pathlib import Path

RESULTS: list[dict[str, object]] = []


def _check(name: str, fn: Callable[[], tuple[str, object]]) -> None:
    t0 = time.perf_counter()
    try:
        status, detail = fn()
    except Exception as exc:
        status, detail = "fail", f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
    RESULTS.append({"check": name, "status": status, "detail": detail, "seconds": round(time.perf_counter() - t0, 3)})


def _core() -> tuple[str, object]:
    from PIL import Image

    from lexoplay.dedupe import LineMemory
    from lexoplay.geometry import Rect
    from lexoplay.ocr_model import Line, OcrResult, Word
    from lexoplay.pipeline import Pipeline, Target
    from lexoplay.translate import TranslateResult

    class Ocr:
        async def recognize(self, prepared):  # type: ignore[no-untyped-def]
            return OcrResult((Line((Word("Hello", Rect(0, 0, 0.1, 0.1)), Word("there.", Rect(0.1, 0, 0.1, 0.1)))),))

    class Tr:
        async def translate(self, req):  # type: ignore[no-untyped-def]
            return TranslateResult(req.text, "Привет.")

    async def direct(fn):  # type: ignore[no-untyped-def]
        return fn()

    async def nosleep(_):  # type: ignore[no-untyped-def]
        return None

    p = Pipeline(Ocr(), Tr(), LineMemory(), sleep=nosleep, run_sync=direct)
    out = asyncio.run(p.run(lambda: Image.new("RGB", (800, 600), "white"), Target("t", Rect(0, 0.7, 1, 0.3))))
    return ("ok" if out.kind == "translated" else "fail"), out.kind


def _test_frame():  # type: ignore[no-untyped-def]
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (800, 600), (30, 36, 51))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((40, 450, 760, 580), 10, fill=(250, 248, 242))
    font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arial.ttf"
    font = ImageFont.truetype(str(font_path), 22) if font_path.exists() else ImageFont.load_default()
    d.text((64, 470), "Mira", fill=(83, 74, 183), font=font)
    d.text((64, 505), "I was reluctant to come here tonight, you know.", fill=(40, 40, 40), font=font)
    d.text((64, 538), "This place feels eerie after dark.", fill=(40, 40, 40), font=font)
    return img


def _ocr(out_dir: Path) -> Callable[[], tuple[str, object]]:
    def run() -> tuple[str, object]:
        from lexoplay.bg import BackgroundLoop
        from lexoplay.geometry import Rect
        from lexoplay.preprocess import prepare
        from lexoplay.textproc import to_utterance
        from lexoplay.win.ocr import OcrUnavailable, WinOcr, available_languages

        bg = BackgroundLoop("selftest-ocr")
        ocr = WinOcr("en-US")

        async def go():  # type: ignore[no-untyped-def]
            langs = available_languages()
            try:
                ocr.check()
            except OcrUnavailable as exc:
                return None, langs, str(exc)
            frame = _test_frame()
            frame.save(out_dir / "ocr-frame.png")
            region = Rect(0.05, 0.75, 0.9, 0.22)
            prepared = prepare(frame, region)
            prepared.image.save(out_dir / "ocr-prepared.png")
            res = await ocr.recognize(prepared)
            return res, langs, None

        try:
            res, langs, err = bg.run(go(), timeout=60)
        finally:
            bg.stop()
        if res is None:
            return "warn", {"languages": langs, "error": err}
        utt = to_utterance(res)
        text = utt.text.lower()
        words = res.words
        inside = all(0.0 <= w.rect.x <= 1 and 0.7 <= w.rect.y <= 1 for w in words)
        ok = "reluctant" in text and "eerie" in text and utt.speaker == "Mira" and inside
        return ("ok" if ok else "fail"), {
            "languages": langs,
            "speaker": utt.speaker,
            "text": utt.text,
            "words": len(words),
            "first_word_rect": words[0].rect.to_dict() if words else None,
        }

    return run


def pixel_frame(lines: list[str], seed: int = 3):  # type: ignore[no-untyped-def]
    """Кадр «как Hotline Miami»: пиксельные цветные буквы с обводкой на тёмном фоне, чуть пляшут."""
    import random

    from PIL import Image, ImageDraw, ImageFont

    rnd = random.Random(seed)
    font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arial.ttf"
    font = ImageFont.truetype(str(font_path), 11) if font_path.exists() else ImageFont.load_default()
    small = Image.new("RGB", (300, 40), (18, 6, 34))
    d = ImageDraw.Draw(small)
    d.fontmode = "1"  # без сглаживания: настоящий пиксельный шрифт
    colors = [(255, 60, 170), (60, 200, 255), (255, 220, 60), (120, 90, 255), (255, 90, 60)]
    y = 4
    for line in lines:
        x = 6
        for i, ch in enumerate(line):
            dy = rnd.choice((-1, 0, 0, 1))
            for ox, oy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                d.text((x + ox, y + dy + oy), ch, fill=(0, 0, 0), font=font)
            d.text((x, y + dy), ch, fill=colors[(i // 3) % len(colors)], font=font)
            x += d.textlength(ch, font=font) + 1
        y += 17
    frame = Image.new("RGB", (1280, 720), (40, 10, 60))
    frame.paste(small.resize((1200, 160), Image.Resampling.NEAREST), (40, 520))
    return frame


def vn_color_frame():  # type: ignore[no-untyped-def]
    """Кадр «как Danganronpa»: голубой текст с тёмной обводкой, ключевые слова жёлто-оранжевые
    с оранжевой обводкой, без подложки, поверх пёстрой сцены."""
    import random

    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    rnd = random.Random(7)
    candidates = [
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arialbd.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ]
    bold = next((p for p in candidates if p.exists()), None)
    font = ImageFont.truetype(str(bold), 46) if bold else ImageFont.load_default()
    name_font = ImageFont.truetype(str(bold), 36) if bold else font
    bg = Image.new("RGB", (1280, 720), (90, 60, 70))
    d = ImageDraw.Draw(bg)
    for _ in range(140):  # пёстрая сцена: красные флаги, светлые пятна, силуэты
        x, y = rnd.randint(-100, 1280), rnd.randint(-100, 720)
        w, h = rnd.randint(30, 260), rnd.randint(30, 260)
        color = rnd.choice([(200, 40, 50), (230, 210, 215), (40, 35, 40), (150, 120, 130), (240, 240, 235), (90, 80, 85)])
        d.ellipse((x, y, x + w, y + h), fill=color)
    bg = bg.filter(ImageFilter.GaussianBlur(3))
    d = ImageDraw.Draw(bg)

    def blue(xy, text, f=font):  # type: ignore[no-untyped-def]
        d.text(xy, text, font=f, fill=(170, 222, 236), stroke_width=4, stroke_fill=(25, 30, 45))
        return xy[0] + d.textlength(text, font=f)

    def yellow(xy, text):  # type: ignore[no-untyped-def]
        d.text(xy, text, font=font, fill=(235, 110, 20), stroke_width=4, stroke_fill=(190, 60, 10))
        mask = Image.new("L", bg.size, 0)
        ImageDraw.Draw(mask).text(xy, text, font=font, fill=255)
        grad = Image.new("RGB", bg.size)
        gd = ImageDraw.Draw(grad)
        top, height = xy[1], 56
        for yy in range(bg.size[1]):
            t = max(0.0, min(1.0, (yy - top) / height))
            gd.line([(0, yy), (bg.size[0], yy)], fill=(255, round(236 - 90 * t), round(80 - 70 * t)))
        bg.paste(grad, (0, 0), mask)
        return xy[0] + d.textlength(text, font=font)

    d.text((70, 455), "Makoto Naegi", font=name_font, fill=(245, 245, 245), stroke_width=4, stroke_fill=(30, 30, 35))
    x = blue((120, 520), 'Oh, and they mentioned the "')
    yellow((x, 520), "Ultimate Biker Gang")
    x = yellow((120, 590), "Leader")
    blue((x, 590), '" too.')
    return bg


def _ocr_auto(out_dir: Path) -> Callable[[], tuple[str, object]]:
    """Автовыбор распознавания на настоящем Windows OCR: обычная новелла и пиксельный шрифт."""

    def run() -> tuple[str, object]:
        from lexoplay.bg import BackgroundLoop
        from lexoplay.dedupe import LineMemory
        from lexoplay.geometry import Rect
        from lexoplay.ocrscore import quality
        from lexoplay.pipeline import Pipeline, Target
        from lexoplay.preprocess import MODES, prepare
        from lexoplay.translate import TranslateResult
        from lexoplay.win.ocr import OcrUnavailable, WinOcr

        class Tr:
            async def translate(self, req):  # type: ignore[no-untyped-def]
                return TranslateResult(req.text, "-")

        bg = BackgroundLoop("selftest-ocr-auto")
        ocr = WinOcr("en-US")
        cases = {
            "novel": (_test_frame(), Rect(0.05, 0.75, 0.9, 0.22)),
            "pixel": (
                pixel_frame(["YOU'RE NOT LEAVING THIS ROOM ALIVE.", "Who are you people anyway?"]),
                Rect(40 / 1280, 520 / 720, 1200 / 1280, 160 / 720),
            ),
            "vn_color": (vn_color_frame(), Rect(40 / 1280, 440 / 720, 1220 / 1280, 230 / 720)),
        }
        # фото настоящих реплик из игр (есть в репозитории, в exe их нет)
        from PIL import Image as _Image

        samples = Path(__file__).resolve().parents[2] / "tests" / "data"
        for photo in sorted(samples.glob("photo_*.jpg")) if samples.is_dir() else []:
            cases[photo.stem] = (_Image.open(photo).convert("RGB"), Rect(0, 0, 1, 1))

        async def go():  # type: ignore[no-untyped-def]
            try:
                ocr.check()
            except OcrUnavailable as exc:
                return {"error": str(exc)}
            p = Pipeline(ocr, Tr(), LineMemory())
            report: dict[str, object] = {}
            for name, (frame, region) in cases.items():
                frame.save(out_dir / f"ocr-auto-{name}.png")
                each = {}
                for mode in MODES:
                    prep = prepare(frame, region, mode=mode)
                    prep.image.save(out_dir / f"ocr-auto-{name}-{mode}.png")
                    res = await ocr.recognize(prep)
                    each[mode] = {"quality": str(quality(res)), "text": res.raw_text}
                t0 = time.perf_counter()
                reading = await p.recognize(frame, Target("t", region))
                report[f"{name}_seconds"] = round(time.perf_counter() - t0, 3)
                report[name] = {
                    "chosen": reading.mode,
                    "quality": str(reading.quality),
                    "ratio": round(reading.quality.ratio, 3),
                    "text": reading.result.raw_text,
                    "tried": reading.tried,
                    "modes": each,
                }
            # проверка смены реплики (свой движок) в одно время с F2, и два чтения одним движком сразу
            frame, region = cases["novel"]
            t0 = time.perf_counter()
            got = await asyncio.gather(
                p.recognize(frame, Target("t", region)),
                p.recognize(frame, Target("t", region), explore=False, slot=3),
                p.recognize(frame, Target("t", region), explore=False, slot=3),
                ocr.recognize(prepare(frame, region, mode=MODES[0])),
                ocr.recognize(prepare(frame, region, mode=MODES[0])),
                return_exceptions=True,
            )
            texts = [
                (g.result.raw_text if hasattr(g, "result") else g.raw_text).lower()
                for g in got
                if not isinstance(g, BaseException)
            ]
            report["concurrent"] = {
                "seconds": round(time.perf_counter() - t0, 3),
                "errors": [repr(g) for g in got if isinstance(g, BaseException)],
                "texts_ok": len(texts) == len(got) and all("reluctant" in t for t in texts),
            }
            report["parallel"] = p.parallel_ok
            return report

        try:
            report = bg.run(go(), timeout=90)
        finally:
            bg.stop()
        if "error" in report:
            return "warn", report
        novel, pixel, color = report["novel"], report["pixel"], report["vn_color"]
        if novel["ratio"] < 0.8 or "reluctant" not in str(novel["text"]).lower():  # type: ignore[index]
            return "fail", report
        color_text = str(color["text"]).lower()  # type: ignore[index]
        color_ok = all(w in color_text for w in ("mentioned", "ultimate", "biker", "leader", "too"))
        all_modes = all(len(report[c]["tried"]) >= 3 for c in ("novel", "pixel", "vn_color"))  # type: ignore[index]
        if not all_modes:
            return "fail", report  # каким-то способом не прочитали вовсе: подбор способа не работает
        conc = report["concurrent"]
        if conc["errors"] or not conc["texts_ok"] or not report["parallel"]:  # type: ignore[index]
            return "fail", report  # одновременные чтения ломают OCR: F2 после клика стал бы медленным
        return ("ok" if pixel["ratio"] >= 0.75 and color_ok else "warn"), report  # type: ignore[index]

    return run


def _update_swap(out_dir: Path) -> tuple[str, object]:
    """Подмена exe при обновлении: копия exe подменяет себя «новой версией» и перезапускается."""
    import shutil
    import subprocess
    import tempfile

    from lexoplay.updater import clean_env

    if not getattr(sys, "frozen", False):
        return "skip", "только в собранном exe"
    tmp = Path(tempfile.mkdtemp(prefix="lexoplay-update-"))
    exe = tmp / "LexoPlay.exe"
    shutil.copy2(sys.executable, exe)
    shutil.copy2(sys.executable, tmp / "LexoPlay.exe.new")
    marker = tmp / "probe.txt"
    proc = subprocess.run(  # noqa: S603
        [str(exe), "--update-swap-test", str(marker)], env=clean_env(), timeout=60, capture_output=True
    )
    deadline = time.time() + 60
    while time.time() < deadline and not marker.exists():
        time.sleep(0.3)
    time.sleep(1.0)
    info = {
        "swap_exit": proc.returncode,
        "probe": marker.read_text(encoding="utf-8") if marker.exists() else None,
        "files": sorted(p.name for p in tmp.iterdir()),
    }
    ok = (
        proc.returncode == 0
        and marker.exists()
        and exe.exists()
        and not (tmp / "LexoPlay.exe.new").exists()
        and not any(n.endswith(".old") for n in info["files"])  # type: ignore[union-attr]
    )
    return ("ok" if ok else "fail"), info


def _lexo_https() -> tuple[str, object]:
    """Настоящий HTTPS до сервера Lexo: в exe должны быть httpx и сертификаты certifi."""
    import httpx

    from lexoplay.config import Settings, server_url

    server = server_url(Settings())
    info: dict[str, object] = {"server": server}

    async def go() -> None:
        async with httpx.AsyncClient(base_url=server, timeout=15) as c:
            r = await c.get("/health")
            info["health"] = r.status_code
            r2 = await c.get("/desktop/me")
            info["desktop_me"] = r2.status_code
            # 401 = эндпоинты LexoPlay на сервере уже есть, 404 = сервер ещё не обновлён
            info["server_has_lexoplay"] = r2.status_code == 401

    try:
        asyncio.run(go())
    except httpx.HTTPError as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
        return "warn", info
    return ("ok" if info.get("health") == 200 else "warn"), info


def _outbox() -> tuple[str, object]:
    import tempfile

    from lexoplay.lexo import LexoOffline
    from lexoplay.outbox import Outbox
    from lexoplay.store import Store

    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "local.db")
        sent: list[str] = []
        fail = {"left": 1}

        async def send(task):  # type: ignore[no-untyped-def]
            if fail["left"]:
                fail["left"] -= 1
                raise LexoOffline("selftest")
            sent.append(task.payload["lemma"])

        clock = {"t": 100.0}
        box = Outbox(store, send, clock=lambda: clock["t"])
        for w in ("eerie", "reluctant", "make amends"):
            box.enqueue("card", {"lemma": w})
        asyncio.run(box.flush())
        clock["t"] += 10
        asyncio.run(box.flush())
        counts = box.counts()
        store.close()
    ok = sorted(sent) == ["eerie", "make amends", "reluctant"] and counts == (0, 0)
    return ("ok" if ok else "fail"), {"sent": sent, "left": counts}


def _app_flow(out_dir: Path) -> Callable[[], tuple[str, object]]:
    """Настоящий App: привязан к поддельному серверу Lexo, F3 по реплике, слово в Lexo."""

    def run() -> tuple[str, object]:
        import tempfile

        import httpx
        from PySide6.QtWidgets import QApplication

        app_q = QApplication.instance() or QApplication(sys.argv[:1])
        tmp = tempfile.mkdtemp(prefix="lexoplay-selftest-")
        os.environ["LEXOPLAY_HOME"] = tmp
        os.environ["ANTHROPIC_API_KEY"] = "sk-selftest"  # чтобы при старте не открылись настройки

        from lexoplay.app import App, CurrentLine
        from lexoplay.bg import BackgroundLoop
        from lexoplay.config import Settings
        from lexoplay.geometry import Rect
        from lexoplay.lexo import LexoClient
        from lexoplay.ocr_model import Line, OcrResult, Word
        from lexoplay.store import Game, Store
        from lexoplay.textproc import Utterance

        calls: list[tuple[str, str, object]] = []

        def handler(req: httpx.Request) -> httpx.Response:
            body = json.loads(req.content) if req.content else None
            calls.append((req.method, req.url.path, body))
            path = req.url.path
            if path == "/desktop/me":
                user = {"username": "selftest", "native_lang": "ru", "learning_lang": "en", "premium": False}
                return httpx.Response(
                    200, json={"user": user, "usage": {"used": 3, "limit": 50}, "device_name": "CI"}
                )
            if path == "/desktop/vocab":
                return httpx.Response(200, json={"items": ["reluctant"], "server_time": "2026-10-19T00:00:00Z"})
            if path == "/desktop/lookup":
                return httpx.Response(
                    200,
                    json={
                        "lemma": "eerie",
                        "pos": "adj",
                        "ipa": "ˈɪəri",
                        "senses": ["жуткий", "зловещий"],
                        "marked_translation": "",
                        "in_deck": False,
                        "card_id": None,
                    },
                )
            if path == "/desktop/cards":
                return httpx.Response(
                    200,
                    json={"card_id": 1, "text": "eerie", "translation": "жуткий", "status": "created", "deck": "Selftest"},
                )
            if path.endswith("/end"):
                return httpx.Response(200, json={"words_added": 1})
            return httpx.Response(404, json={"detail": "not found"})

        def pump(cond: Callable[[], bool], seconds: float = 5.0) -> bool:
            end = time.time() + seconds
            while time.time() < end:
                app_q.processEvents()
                if cond():
                    return True
                time.sleep(0.02)
            return cond()

        store = Store(Path(tmp) / "local.db")
        region = Rect(0.05, 0.75, 0.9, 0.2)
        # игра уже есть: при старте не откроется окно LexoPlay (оно закрыло бы режим слов посреди проверки)
        game = store.save_game(Game("selftest.exe", "Selftest", region=region))
        bg = BackgroundLoop("selftest-app")
        app = App(app_q, Settings(), store, bg)
        app.client = LexoClient("https://lexo.test", "tok", transport=httpx.MockTransport(handler))
        app._rebuild_translator()
        info: dict[str, object] = {}
        try:
            app.start()
            info["vocab_loaded"] = pump(lambda: app.vocab.known("reluctant"))
            info["account"] = app.account.label if app.account else None

            app.display = Rect(80, 80, 1000, 700)
            session = app.sessions.touch(game.exe, game.title)
            words, x = [], 0.08
            for w in "This place feels eerie after dark.".split():
                words.append(Word(w, Rect(x, 0.85, 0.012 * len(w), 0.035)))
                x += 0.012 * len(w) + 0.012
            ocr = OcrResult((Line(tuple(words)),))
            line = CurrentLine(game.exe, ocr, Utterance(None, "This place feels eerie after dark."), None, time.time())
            app._open_words(game, session, line)
            info["layer_open"] = app.word_layer.is_open
            app._on_word_selected([3])
            info["card_ready"] = pump(lambda: bool(app.word_ctx and app.word_ctx.card and app.word_ctx.card.phase == "ready"))
            app.word_layer.grab().save(str(out_dir / "word-layer.png"))
            app._on_word_add()
            info["card_saved"] = bool(app.word_ctx and app.word_ctx.card and app.word_ctx.card.phase == "saved")
            info["card_sent"] = pump(lambda: any(c[1] == "/desktop/cards" for c in calls) and app.outbox.counts() == (0, 0))
            app.word_layer.finish("esc")
            app.sessions.end_all()
            info["session_end_sent"] = pump(lambda: any(c[1].endswith("/end") for c in calls))
            # окно LexoPlay на настоящем App (контроллер тот же, что в игре)
            app.show_window("games")
            for _ in range(10):
                app_q.processEvents()
            if app.window is not None:
                app.window.grab().save(str(out_dir / "window-real-app.png"))
                info["window_ok"] = app.window.isVisible()
                app.window.hide()
        finally:
            app.shutdown()

        card = next((c[2] for c in calls if c[1] == "/desktop/cards"), None) or {}
        lookup = next((c[2] for c in calls if c[1] == "/desktop/lookup"), None) or {}
        end = next((c for c in calls if c[1].endswith("/end")), None)
        info["card_payload"] = card
        info["paths"] = [f"{m} {p}" for m, p, _ in calls]
        ok = (
            info.get("vocab_loaded")
            and info.get("layer_open")
            and info.get("card_ready")
            and info.get("card_saved")
            and info.get("card_sent")
            and info.get("session_end_sent")
            and lookup.get("selection") == "eerie"
            and lookup.get("sentence") == "This place feels eerie after dark."
            and card.get("lemma") == "eerie"
            and card.get("translation") == "жуткий, зловещий"
            and card.get("session_id") == session.id
            and card.get("idempotency_key")
            and end is not None
            and end[1] == f"/desktop/sessions/{session.id}/end"
        )
        return ("ok" if ok else "fail"), info

    return run


def _own_key_flow(out_dir: Path) -> Callable[[], tuple[str, object]]:
    """Без Lexo, своим ключом: F3 → значение → «+ в мои слова» → слово в «Слова»; после привязки уходит в Lexo."""

    def run() -> tuple[str, object]:
        import tempfile

        import httpx
        from PySide6.QtWidgets import QApplication

        app_q = QApplication.instance() or QApplication(sys.argv[:1])
        tmp = tempfile.mkdtemp(prefix="lexoplay-selftest-own-")
        os.environ["LEXOPLAY_HOME"] = tmp
        os.environ["ANTHROPIC_API_KEY"] = "sk-selftest"

        from lexoplay.app import App, CurrentLine
        from lexoplay.bg import BackgroundLoop
        from lexoplay.config import Settings
        from lexoplay.geometry import Rect
        from lexoplay.lexo import LexoClient, Sense
        from lexoplay.ocr_model import Line, OcrResult, Word
        from lexoplay.store import Game, Store
        from lexoplay.textproc import Utterance

        calls: list[tuple[str, object]] = []

        def handler(req: httpx.Request) -> httpx.Response:
            body = json.loads(req.content) if req.content else None
            calls.append((req.url.path, body))
            if req.url.path == "/desktop/cards":
                return httpx.Response(200, json={"card_id": 1, "text": "eerie", "translation": "жуткий", "status": "created", "deck": "x"})
            if req.url.path.endswith("/end"):
                return httpx.Response(200, json={"words_added": 1})
            if req.url.path == "/desktop/me":
                user = {"username": "selftest", "native_lang": "ru", "learning_lang": "en", "premium": False}
                return httpx.Response(200, json={"user": user, "usage": {"used": 0, "limit": 50}, "device_name": "CI"})
            if req.url.path == "/desktop/vocab":
                return httpx.Response(200, json={"items": [], "server_time": "2026-10-19T00:00:00Z"})
            return httpx.Response(404, json={"detail": "not found"})

        from lexoplay.lookup import ExplainItem, Explanation

        class FakeLookup:
            last_usage = (120, 30)

            async def lookup(self, **kw):  # type: ignore[no-untyped-def]
                calls.append(("anthropic-lookup", kw))
                return Sense("eerie", "adj", "ˈɪəri", ["жуткий", "зловещий"])

            async def explain(self, **kw):  # type: ignore[no-untyped-def]
                calls.append(("anthropic-explain", kw))
                return Explanation(
                    (
                        ExplainItem("feels eerie", "кажется жутким", "feel + прилагательное: «ощущается каким-то»", "feel eerie"),
                        ExplainItem("after dark", "после наступления темноты", "устойчивое выражение"),
                    ),
                    "Героиня признаётся, что ей не по себе: днём место обычное, а с темнотой становится жутким.",
                    "Это место ощущается жутким после темноты.",
                )

        def pump(cond: Callable[[], bool], seconds: float = 5.0) -> bool:
            end = time.time() + seconds
            while time.time() < end:
                app_q.processEvents()
                if cond():
                    return True
                time.sleep(0.02)
            return cond()

        store = Store(Path(tmp) / "local.db")
        game = store.save_game(Game("selftest.exe", "Selftest", region=Rect(0.05, 0.75, 0.9, 0.2)))
        bg = BackgroundLoop("selftest-own")
        app = App(app_q, Settings(), store, bg)
        app.client = LexoClient("https://lexo.test", None, transport=httpx.MockTransport(handler))
        app._rebuild_translator()
        info: dict[str, object] = {"translator": type(app.translator).__name__}
        app.own_lookup = FakeLookup()  # type: ignore[assignment]
        try:
            app.start()
            app.display = Rect(80, 80, 1000, 700)
            session = app.sessions.touch(game.exe, game.title)
            words, x = [], 0.08
            for w in "This place feels eerie after dark.".split():
                words.append(Word(w, Rect(x, 0.85, 0.012 * len(w), 0.035)))
                x += 0.012 * len(w) + 0.012
            line = CurrentLine(game.exe, OcrResult((Line(tuple(words)),)), Utterance(None, "This place feels eerie after dark."), None, time.time())
            app._open_words(game, session, line)
            app._on_word_selected([3])
            info["card_ready"] = pump(lambda: bool(app.word_ctx and app.word_ctx.card and app.word_ctx.card.phase == "ready"))
            info["button"] = app.word_ctx.card.button if app.word_ctx and app.word_ctx.card else None
            app.word_layer.grab().save(str(out_dir / "word-layer-own-key.png"))
            app._on_word_add()
            saved = store.words_list()
            info["saved_local"] = [(w.word, w.translation, w.lexo) for w in saved]
            # F7: разбор реплики, пункт разбора в слова
            app._request_explain()
            info["explain_ready"] = pump(
                lambda: bool(app.word_ctx and app.word_ctx.explain and app.word_ctx.explain.phase == "ready")
            )
            app.word_layer.grab().save(str(out_dir / "explain-panel.png"))
            app._on_explain_save(1)
            info["explain_saved"] = [w.word for w in store.words_list()][:1]
            info["outbox_before_link"] = app.outbox.counts()
            info["usage_words"] = app.own_usage.today().words
            from datetime import date as _date

            info["stats_words_today"] = store.stats_days(_date.today().isoformat()).get(_date.today().isoformat(), (0, 0, 0))[1]
            app.word_layer.finish("esc")
            info["server_calls_before_link"] = [c[0] for c in calls if c[0].startswith("/")]
            # привязали ПК: слова сами уходят в Lexo
            app.client.token = "tok"
            app._push_local_words()
            info["sent_after_link"] = pump(lambda: any(c[0] == "/desktop/cards" for c in calls) and app.outbox.counts() == (0, 0))
            info["marked"] = [w.lexo for w in store.words_list()]
            app.show_window("words")
            for _ in range(10):
                app_q.processEvents()
            if app.window is not None:
                app.window.grab().save(str(out_dir / "window-words-real-app.png"))
                app.window.hide()
        finally:
            app.shutdown()
        card = next((c[1] for c in calls if c[0] == "/desktop/cards"), None) or {}
        info["card_payload"] = card
        ok = (
            info["card_ready"]
            and info["button"] == "+ в мои слова"
            and info["saved_local"] == [("eerie", "жуткий, зловещий", False)]
            and info["explain_ready"]
            and info["explain_saved"] == ["after dark"]
            and info["outbox_before_link"] == (0, 0)
            and info["usage_words"] == 1
            and not info["server_calls_before_link"]
            and info["sent_after_link"]
            and info["marked"] == [True, True]
            and info["stats_words_today"] == 2
            and card.get("lemma") == "eerie"
            and card.get("sentence") == "This place feels eerie after dark."
        )
        return ("ok" if ok else "fail"), info

    return run


def _qt_checks(out_dir: Path) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QApplication, QWidget

    from lexoplay.geometry import Rect
    from lexoplay.ui.overlay import Overlay

    app = QApplication.instance() or QApplication(sys.argv[:1])

    def overlay() -> tuple[str, object]:
        ov = Overlay()
        ov.set_display_rect(Rect(40, 40, 960, 720))
        ov.show_text(Rect(0.04, 0.78, 0.92, 0.18), "Знаешь, мне совсем не хотелось сюда сегодня приходить. После заката здесь как-то жутко.")
        ov.toast("eerie сохранено в Lexo")
        end = time.monotonic() + 0.4  # плашка появляется плавно: ждём, пока проявится
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.02)
        img = ov.grab().toImage()
        img.save(str(out_dir / "overlay.png"))
        painted = sum(
            1 for y in range(0, img.height(), 6) for x in range(0, img.width(), 6) if img.pixelColor(x, y).alpha() > 0
        )
        ov.hide_strip()
        end = time.monotonic() + 0.4
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.02)
        hidden_ok = ov.strip_visible is False and ov._ghost is None
        ov.close()
        return ("ok" if painted > 50 and hidden_ok else "fail"), {"painted_samples": painted, "size": [img.width(), img.height()]}

    _check("overlay_render", overlay)

    def word_layer() -> tuple[str, object]:
        from lexoplay.ocr_model import Line, OcrResult, Word
        from lexoplay.ui.word_layer import WordLayer
        from lexoplay.wordmode import CardState, build_views

        words, x = [], 0.08
        for w in "This place feels eerie after dark.".split():
            words.append(Word(w, Rect(x, 0.86, 0.012 * len(w), 0.035)))
            x += 0.012 * len(w) + 0.012
        views = build_views(OcrResult((Line(tuple(words)),)))
        layer = WordLayer()
        layer.open(Rect(40, 40, 960, 720), Rect(0.04, 0.78, 0.92, 0.18), views)
        layer.select([3])
        layer.set_card(CardState("ready", "eerie", "ˈɪəri", "adj", ["жуткий", "зловещий"]))
        app.processEvents()
        img = layer.grab().toImage()
        img.save(str(out_dir / "word-layer-only.png"))
        has_button = layer._button_rect is not None  # noqa: SLF001
        layer.finish("closed")
        layer.close()
        return ("ok" if has_button else "fail"), {"size": [img.width(), img.height()]}

    _check("word_layer_render", word_layer)

    def main_window() -> tuple[str, object]:
        import tempfile

        from lexoplay.config import Settings
        from lexoplay.lexo import Deck
        from lexoplay.store import Game, Store
        from lexoplay.ui import theme
        from lexoplay.ui.main_window import AccountStatus, MainWindow

        from types import SimpleNamespace

        from lexoplay.store import SavedWord
        from lexoplay.ui.main_window import UpdateState

        family = theme.load_fonts()
        tmp = Path(tempfile.mkdtemp(prefix="lexoplay-ui-"))
        store = Store(tmp / "local.db")
        store.save_game(Game("dr1_us.exe", "Danganronpa: Trigger Happy Havoc", region=Rect(0.05, 0.7, 0.9, 0.25)))
        store.save_game(Game("midnight.exe", "Midnight Station", deck_mode="deck", deck_id=5, deck_title="Слова из игр"))
        store.line_add("dr1_us.exe", "ru", "a", "Makoto", "I wonder what kind of school this is...", "Интересно, что это за школа...")
        store.line_add("dr1_us.exe", "ru", "b", "Monokuma", "Puhuhu! Welcome to despair!", "Пухуху! Добро пожаловать в отчаяние!")
        # вчерашняя сессия: история показывает реплики по сессиям, одна в избранном
        older = time.time() - 26 * 3600
        for i, (sp, src, tr) in enumerate(
            [
                ("Kyoko", "Don't jump to conclusions. Look at the evidence first.", "Не спеши с выводами. Сначала посмотри на улики."),
                ("Makoto", "My view became warped, twisted.", "Всё перед глазами поплыло и исказилось."),
            ]
        ):
            store.line_add("dr1_us.exe", "ru", f"old{i}", sp, src, tr)
            with store._lock:
                store._db.execute("UPDATE lines SET created_at = ?, seen_at = ? WHERE norm = ?", (older + i * 90, older + i * 90, f"old{i}"))
                store._db.commit()
        store.line_star(store.lines_list(query="warped")[0].id, True)
        store.word_add(
            SavedWord("dr1_us.exe", "Danganronpa", "despair", "despair", "отчаяние, безысходность", "dɪˈspɛr", "noun",
                      "Puhuhu! Welcome to despair!", "Пухуху! Добро пожаловать в отчаяние!", created_at=time.time())
        )
        shot = pixel_frame(["WELCOME TO HOPE'S PEAK ACADEMY!"]).crop((40, 520, 1240, 680))
        from datetime import date, timedelta

        for back, (lines, words, secs) in enumerate([(46, 7, 3900), (12, 2, 1200), (0, 0, 0), (31, 4, 2700), (58, 11, 5100), (22, 3, 1500), (9, 1, 600)]):
            day = (date.today() - timedelta(days=back)).isoformat()
            store.stats_add(day, "dr1_us.exe", lines=lines, words=words, seconds=secs)

        class Ctl:
            settings = Settings()

            def __init__(self) -> None:
                self.store = store
                self.update = UpdateState("available", SimpleNamespace(version="0.4.1"))

            def account_status(self):  # type: ignore[no-untyped-def]
                return AccountStatus(True, "@ruslan", used=12, limit=50, queue=1)

            def load_decks(self, done):  # type: ignore[no-untyped-def]
                done([Deck(3, "Danganronpa: Trigger Happy Havoc", 41, 6, 3), Deck(5, "Слова из игр", 120, 18, 0)], 5, None)

            def active_exe(self):  # type: ignore[no-untyped-def]
                return "dr1_us.exe"

            def api_key(self):  # type: ignore[no-untyped-def]
                return ""

            def usage_summary(self):  # type: ignore[no-untyped-def]
                return "Сегодня реплик: 42, слов: 3. Это примерно меньше цента (18 400 токенов)."

            def history(self, exe=None, query="", limit=100, offset=0):  # type: ignore[no-untyped-def]
                return store.lines_list(exe, query, limit, offset)

            def saved_words(self):  # type: ignore[no-untyped-def]
                return store.words_list()

            def speakers(self, exe):  # type: ignore[no-untyped-def]
                return store.line_speakers(exe)

            def glossary(self, exe):  # type: ignore[no-untyped-def]
                return store.glossary(exe)

            def get_last_read(self, exe):  # type: ignore[no-untyped-def]
                if exe != "dr1_us.exe":
                    return None
                return SimpleNamespace(image=shot, text="WELCOME TO HOPE'S PEAK ACADEMY!", mode_name="пиксельный шрифт")

            def autostart_supported(self):  # type: ignore[no-untyped-def]
                return True

            def autostart_enabled(self):  # type: ignore[no-untyped-def]
                return False

            def progress(self):  # type: ignore[no-untyped-def]
                from lexoplay.progress import progress

                return progress(store, 10)

            def __getattr__(self, _name):  # type: ignore[no-untyped-def]
                return lambda *a, **k: None

        w = MainWindow(Ctl())
        w.resize(1000, 700)
        w.show()
        saved = []
        for key in ("games", "history", "words", "progress", "lexo", "keys", "view", "more"):
            w.show_page(key)
            for _ in range(5):
                app.processEvents()
            name = f"window-{key}.png"
            w.grab().save(str(out_dir / name))
            saved.append(name)
        w.hide()
        store.close()
        return ("ok" if family.startswith("Segoe UI") else "warn"), {"font": family, "display": theme.DISPLAY_FAMILY, "pages": saved}

    _check("main_window_render", main_window)

    def picker_freeze() -> tuple[str, object]:
        from PySide6.QtGui import QImage

        from lexoplay.ui.region_picker import RegionPicker

        frame = _test_frame().convert("RGB")
        raw = frame.tobytes("raw", "RGB")
        bg = QImage(raw, frame.width, frame.height, frame.width * 3, QImage.Format.Format_RGB888).copy()
        picker = RegionPicker(Rect(40, 40, 800, 600), "Тестовая новелла", Rect(0.05, 0.75, 0.9, 0.22), None, background=bg)
        picker.open()
        app.processEvents()
        img = picker.grab().toImage()
        img.save(str(out_dir / "picker.png"))
        center = img.pixelColor(img.width() // 2, int(img.height() * 0.86))
        picker._done = True  # noqa: SLF001
        picker.close()
        # в центре окна диалога видно светлый стоп-кадр под затемнением, а не чёрную заливку
        ok = center.red() > 90
        return ("ok" if ok else "fail"), {"center_rgb": [center.red(), center.green(), center.blue()]}

    _check("picker_freeze_frame", picker_freeze)

    if sys.platform != "win32":
        return

    def capture() -> tuple[str, object]:
        from lexoplay.win import capture as cap
        from lexoplay.win import winapi

        w = QWidget(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        pal = w.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(220, 30, 30))
        w.setPalette(pal)
        w.setAutoFillBackground(True)
        w.setGeometry(120, 120, 320, 200)
        w.show()
        for _ in range(20):
            app.processEvents()
            time.sleep(0.02)
        hwnd = int(w.winId())
        info: dict[str, object] = {"client": winapi.client_rect_screen(hwnd).to_dict()}
        img = cap.grab_window(hwnd)
        img.save(out_dir / "capture-window.png")
        px = img.getpixel((img.width // 2, img.height // 2))
        info["window_size"] = img.size
        info["window_center"] = px
        win_ok = px[0] > 180 and px[1] < 80
        try:
            scr = cap.grab_screen(winapi.client_rect_screen(hwnd))
            scr.save(out_dir / "capture-screen.png")
            info["screen_center"] = scr.getpixel((scr.width // 2, scr.height // 2))
        except Exception as exc:
            info["screen_error"] = str(exc)
        info["foreground"] = winapi.foreground_window()
        info["exe"] = winapi.process_exe(winapi.window_pid(hwnd))
        w.close()
        return ("ok" if win_ok else "warn"), info

    _check("capture", capture)

    def hotkey() -> tuple[str, object]:
        from lexoplay.hotkeys import parse
        from lexoplay.win.hooks import HotkeyManager, WindowEvents

        hm = HotkeyManager()
        hm.define("t", parse("Ctrl+Shift+F24"), "game")
        fails: list[str] = []
        hm.failed.connect(lambda n, t: fails.append(t))
        hm.set_group("game", True)
        hm.set_group("game", False)
        ev = WindowEvents()
        ev.close()
        return ("ok" if not fails else "warn"), {"failed": fails}

    _check("hotkeys_and_hooks", hotkey)

    def words_keys() -> tuple[str, object]:
        from lexoplay.hotkeys import parse
        from lexoplay.win.hooks import HotkeyManager

        hm = HotkeyManager()
        fails: list[str] = []
        hm.failed.connect(lambda n, t: fails.append(t))
        hm.define("esc", parse("Escape"), "words")
        hm.define("enter", parse("Enter"), "words")
        hm.define("f6", parse("F6"), "game")
        hm.set_group("words", True)
        hm.set_group("game", True)
        hm.set_group("words", False)
        hm.set_group("game", False)
        return ("ok" if not fails else "warn"), {"failed": fails}

    _check("word_mode_keys", words_keys)

    def raw_input() -> tuple[str, object]:
        import ctypes

        from lexoplay.win import input as rawin

        info: dict[str, object] = {
            "sizes": [ctypes.sizeof(rawin.RAWINPUTHEADER), ctypes.sizeof(rawin.RAWMOUSE), ctypes.sizeof(rawin.RAWKEYBOARD)]
        }
        iw = rawin.InputWatch()
        got: list[int] = []
        iw.pressed.connect(lambda kind: got.append(kind))
        info["started"] = iw.start()
        u32 = ctypes.windll.user32

        def pump(sec: float) -> None:
            end = time.time() + sec
            while time.time() < end:
                app.processEvents()
                time.sleep(0.01)

        iw.ignore_vks = {0x86}  # F23 считается «нашей» клавишей: игнор
        u32.keybd_event(0x86, 0, 0, 0)
        u32.keybd_event(0x86, 0, 2, 0)
        pump(0.4)
        info["ignored_key_events"] = len(got)
        iw.ignore_vks = set()
        u32.keybd_event(0x86, 0, 0, 0)
        u32.keybd_event(0x86, 0, 2, 0)
        pump(0.4)
        info["key_events"] = len(got)
        u32.mouse_event(0x0800, 0, 0, 120, 0)  # колесо
        pump(0.4)
        info["after_wheel"] = len(got)
        info["kinds"] = sorted(set(got))
        iw.close()
        # клик в окно LexoPlay не считается «игрок пропустил реплику»
        from PySide6.QtWidgets import QWidget as _W

        from lexoplay.win import winapi as _wa

        probe = _W()
        probe.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        probe.setGeometry(300, 300, 200, 120)
        probe.show()
        pump(0.3)
        u32.SetCursorPos(400, 360)
        pump(0.1)
        info["cursor_over_own"] = _wa.cursor_over_own_window()
        probe.hide()
        pump(0.2)
        info["cursor_over_own_after_hide"] = _wa.cursor_over_own_window()
        ok = (
            info["started"]
            and info["ignored_key_events"] == 0
            and info["key_events"] >= 1
            and info["after_wheel"] >= 2
            and info["kinds"] == ["key", "mouse"]
            and info["cursor_over_own"]
            and not info["cursor_over_own_after_hide"]
        )
        return ("ok" if ok else "warn"), info

    _check("raw_input_player_actions", raw_input)

    def cursor_unlock() -> tuple[str, object]:
        """Игра держит курсор (пока в фокусе, возвращает его в центр): режим слов замечает и забирает фокус."""
        import ctypes

        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QWidget as _W

        from lexoplay.cursorguard import CursorGuard
        from lexoplay.geometry import Rect as _R
        from lexoplay.ui.word_layer import WordLayer
        from lexoplay.win import input as rawin
        from lexoplay.win import winapi as _wa

        u32 = ctypes.windll.user32

        def pump(sec: float) -> None:
            end = time.time() + sec
            while time.time() < end:
                app.processEvents()
                time.sleep(0.005)

        def native(h: int) -> None:
            _wa.exclude_from_capture(h)
            _wa.set_no_activate(h)

        game = _W()
        game.setWindowTitle("LexoPlay cursor test")
        game.setGeometry(200, 200, 600, 400)
        game.show()
        pump(0.3)
        ghwnd = int(game.winId())
        _wa.force_foreground(ghwnd)
        pump(0.3)
        center = (500, 400)

        def lock() -> None:  # как игра: пока она в фокусе, курсор каждый кадр в центр
            if _wa.root_window(_wa.foreground_window()) == ghwnd:
                u32.SetCursorPos(*center)

        locker = QTimer()
        locker.setInterval(10)
        locker.timeout.connect(lock)
        locker.start()
        iw = rawin.InputWatch()
        iw.start()
        layer = WordLayer(on_native=native)
        layer.open(_R(200, 200, 600, 400), _R(0.05, 0.7, 0.9, 0.25), [])
        info: dict[str, object] = {"game_in_focus": _wa.root_window(_wa.foreground_window()) == ghwnd}
        pump(0.1)
        guard = CursorGuard(_wa.cursor_pos, lambda: iw.travel, counts=lambda p: not _wa.at_screen_edge(p))
        guard.reset()
        detected = took = False
        end = time.time() + 3
        while time.time() < end and not detected:
            u32.mouse_event(0x0001, 30, 0, 0, 0)  # MOUSEEVENTF_MOVE: сдвиг, как от настоящей мыши
            pump(0.05)
            detected = guard.tick()
        info["detected"] = detected
        info["travel"] = iw.travel
        info["exclusive_fullscreen"] = _wa.exclusive_fullscreen()
        if detected:
            lh = int(layer.winId())
            _wa.set_no_activate(lh, False)
            _wa.force_foreground(lh)
            pump(0.3)
            took = _wa.root_window(_wa.foreground_window()) == lh
        info["took_focus"] = took
        start = _wa.cursor_pos()
        for _ in range(10):
            u32.mouse_event(0x0001, 15, 0, 0, 0)
            pump(0.03)
        info["cursor_moves_after"] = _wa.cursor_pos() != start
        locker.stop()
        layer.finish("esc")
        layer.close()
        iw.close()
        game.close()
        pump(0.1)
        ok = detected and took and info["cursor_moves_after"]
        return ("ok" if ok else "fail"), info

    _check("cursor_unlock_in_word_mode", cursor_unlock)

    def no_activate() -> tuple[str, object]:
        import ctypes

        from lexoplay.ui.overlay import Overlay
        from lexoplay.ui.word_layer import WordLayer
        from lexoplay.win import winapi

        def overlay_native(h: int) -> None:
            winapi.exclude_from_capture(h)
            winapi.set_no_activate(h)

        info = {}
        for name, widget in (("overlay", Overlay(on_native=overlay_native)), ("word_layer", WordLayer(on_native=overlay_native))):
            hwnd = int(widget.winId())
            style = ctypes.windll.user32.GetWindowLongPtrW(hwnd, winapi.GWL_EXSTYLE)
            info[name] = bool(style & winapi.WS_EX_NOACTIVATE)
            widget.close()
        return ("ok" if all(info.values()) else "fail"), info

    _check("overlays_never_take_focus", no_activate)

    def windows_list() -> tuple[str, object]:
        from lexoplay.games import WindowInfo, rank
        from lexoplay.win import winapi

        raw = winapi.app_windows()
        infos = [WindowInfo(h, t, e, p, int(r.w), int(r.h), m) for h, t, e, p, r, m in raw]
        return "ok", {"all": [(w.title[:40], w.exe) for w in infos][:15], "games": [w.exe for w in rank(infos)][:15]}

    _check("window_list", windows_list)

    def magpie_math() -> tuple[str, object]:
        from lexoplay.win import winapi

        w = QWidget()
        w.setGeometry(200, 200, 400, 300)
        w.show()
        app.processEvents()
        hwnd = int(w.winId())
        rect, scaled = winapi.display_rect(hwnd, "always")
        mon = winapi.monitor_rect(hwnd)
        w.close()
        ok = scaled and rect.h <= mon.h + 1 and rect.w <= mon.w + 1
        return ("ok" if ok else "fail"), {"display": rect.to_dict(), "monitor": mon.to_dict()}

    _check("display_rect", magpie_math)
    _check("app_word_flow", _app_flow(out_dir))
    _check("app_own_key_flow", _own_key_flow(out_dir))


def run(report: str) -> int:
    out = Path(report).resolve()
    out_dir = out.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    from lexoplay import __version__

    _check("core_pipeline", _core)

    def imports() -> tuple[str, object]:
        import importlib

        mods = [
            "lexoplay.watch",
            "lexoplay.translate",
            "lexoplay.store",
            "lexoplay.lexo",
            "lexoplay.outbox",
            "lexoplay.session",
            "lexoplay.vocab",
            "lexoplay.wordmode",
            "lexoplay.ui.overlay",
            "lexoplay.ui.region_picker",
            "lexoplay.ui.main_window",
            "lexoplay.ui.window_picker",
            "lexoplay.ui.hotkey_edit",
            "lexoplay.ui.word_layer",
            "lexoplay.report",
            "lexoplay.games",
            "lexoplay.updater",
            "lexoplay.autostart",
            "lexoplay.lookup",
            "lexoplay.export",
            "lexoplay.history",
            "lexoplay.usage",
            "lexoplay.ocrscore",
            "lexoplay.wordlist_en",
            "lexoplay.ui.pages",
            "lexoplay.ui.history_page",
            "lexoplay.ui.progress_page",
            "lexoplay.timeline",
            "lexoplay.progress",
        ]
        if sys.platform == "win32":
            mods += ["lexoplay.win.input", "lexoplay.app", "lexoplay.win.winapi", "lexoplay.win.capture", "lexoplay.win.hooks", "lexoplay.win.ocr"]
        for m in mods:
            importlib.import_module(m)
        import anthropic  # noqa: F401

        return "ok", mods

    _check("imports", imports)
    _check("outbox", _outbox)
    _check("lexo_https", _lexo_https)
    if sys.platform == "win32":

        def keyring_roundtrip() -> tuple[str, object]:
            from lexoplay.config import _keyring

            kr = _keyring()
            kr.set_password("LexoPlay-selftest", "probe", "secret-123")
            got = kr.get_password("LexoPlay-selftest", "probe")
            kr.delete_password("LexoPlay-selftest", "probe")
            return ("ok" if got == "secret-123" else "fail"), type(kr.get_keyring()).__name__

        _check("keyring", keyring_roundtrip)
        _check("ocr", _ocr(out_dir))
        _check("ocr_auto_modes", _ocr_auto(out_dir))
        _check("update_swap", lambda: _update_swap(out_dir))

        def autostart_registry() -> tuple[str, object]:
            from lexoplay import autostart

            if not autostart.supported():
                return "skip", "только в собранном exe"
            before = autostart.enabled()
            autostart.set_enabled(True)
            on = autostart.enabled() and autostart._read() == autostart.command()  # noqa: SLF001
            autostart.set_enabled(before)
            return ("ok" if on and autostart.enabled() == before else "fail"), {"command": autostart.command()}

        _check("autostart", autostart_registry)
    else:
        RESULTS.append({"check": "ocr", "status": "skip", "detail": "не Windows"})
    _qt_checks(out_dir)

    failed = [r for r in RESULTS if r["status"] == "fail"]
    data = {"version": __version__, "python": sys.version, "frozen": bool(getattr(sys, "frozen", False)), "results": RESULTS}
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if sys.stdout is not None:
        try:  # консоль CI в cp1252: русский текст в деталях иначе роняет печать
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
        for r in RESULTS:
            print(f"[{r['status']:>4}] {r['check']}: {str(r['detail'])[:300]}")
    return 1 if failed else 0
