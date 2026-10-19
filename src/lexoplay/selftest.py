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
        bg = BackgroundLoop("selftest-app")
        app = App(app_q, Settings(), store, bg)
        app.client = LexoClient("https://lexo.test", "tok", transport=httpx.MockTransport(handler))
        app._rebuild_translator()
        info: dict[str, object] = {}
        try:
            app.start()
            info["vocab_loaded"] = pump(lambda: app.vocab.known("reluctant"))
            info["account"] = app.account.label if app.account else None

            region = Rect(0.05, 0.75, 0.9, 0.2)
            game = store.save_game(Game("selftest.exe", "Selftest", region=region))
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
        app.processEvents()
        img = ov.grab().toImage()
        img.save(str(out_dir / "overlay.png"))
        painted = sum(
            1 for y in range(0, img.height(), 6) for x in range(0, img.width(), 6) if img.pixelColor(x, y).alpha() > 0
        )
        ov.hide_strip()
        app.processEvents()
        hidden_ok = ov.strip_visible is False
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


def run(report: str) -> int:
    out = Path(report).resolve()
    out_dir = out.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    from lexoplay import __version__

    _check("core_pipeline", _core)

    def imports() -> tuple[str, object]:
        import importlib

        mods = [
            "lexoplay.translate",
            "lexoplay.store",
            "lexoplay.lexo",
            "lexoplay.outbox",
            "lexoplay.session",
            "lexoplay.vocab",
            "lexoplay.wordmode",
            "lexoplay.ui.overlay",
            "lexoplay.ui.region_picker",
            "lexoplay.ui.settings_dialog",
            "lexoplay.ui.word_layer",
        ]
        if sys.platform == "win32":
            mods += ["lexoplay.app", "lexoplay.win.winapi", "lexoplay.win.capture", "lexoplay.win.hooks", "lexoplay.win.ocr"]
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
    else:
        RESULTS.append({"check": "ocr", "status": "skip", "detail": "не Windows"})
    _qt_checks(out_dir)

    failed = [r for r in RESULTS if r["status"] == "fail"]
    data = {"version": __version__, "python": sys.version, "frozen": bool(getattr(sys, "frozen", False)), "results": RESULTS}
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if sys.stdout is not None:
        for r in RESULTS:
            print(f"[{r['status']:>4}] {r['check']}: {str(r['detail'])[:300]}")
    return 1 if failed else 0
