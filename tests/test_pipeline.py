import asyncio

from PIL import Image, ImageDraw

from lexoplay.dedupe import LineMemory
from lexoplay.geometry import Rect
from lexoplay.ocr_model import Line, OcrResult, Word
from lexoplay.pipeline import CaptureError, Pipeline, Target
from lexoplay.translate import TranslateRequest, TranslateResult, TranslationError

REGION = Rect(0.05, 0.78, 0.9, 0.18)


def frame(text: str) -> Image.Image:
    img = Image.new("RGB", (800, 600), (250, 248, 242))
    ImageDraw.Draw(img).text((60, 500), text, fill=(0, 0, 0))
    return img


def result(*lines: str) -> OcrResult:
    return OcrResult(
        tuple(Line(tuple(Word(w, Rect(0, 0, 0.01, 0.01)) for w in t.split())) for t in lines)
    )


class FakeOcr:
    def __init__(self, res: OcrResult) -> None:
        self.res = res
        self.calls = 0

    async def recognize(self, prepared):
        self.calls += 1
        return self.res


class FakeTranslator:
    def __init__(self, fail: str | None = None) -> None:
        self.requests: list[TranslateRequest] = []
        self.fail = fail

    async def translate(self, req):
        self.requests.append(req)
        if self.fail:
            raise TranslationError(self.fail)
        return TranslateResult(source=req.text.replace("l was", "I was"), translation=f"RU:{req.text}")


async def no_sleep(_):
    return None


async def direct(fn):
    return fn()


def make(ocr_res, translator=None, frames=None):
    seq = list(frames or [frame("same text")])
    calls = {"n": 0}

    def grab():
        i = min(calls["n"], len(seq) - 1)
        calls["n"] += 1
        return seq[i]

    p = Pipeline(FakeOcr(ocr_res), translator or FakeTranslator(), LineMemory(), sleep=no_sleep, run_sync=direct)
    return p, grab, calls


def run(coro):
    return asyncio.run(coro)


def test_translates_and_remembers():
    p, grab, _ = make(result("Mira", "l was reluctant to come here."))
    out = run(p.run(grab, Target("Midnight Station", REGION)))
    assert out.kind == "translated"
    assert out.record.speaker == "Mira"
    assert out.record.source == "I was reluctant to come here."
    assert out.thumb is not None

    again = run(p.run(grab, Target("Midnight Station", REGION)))
    assert again.kind == "repeat" and again.record.line_id == out.record.line_id
    assert len(p.translator.requests) == 1  # повтор без запроса


def test_context_is_passed():
    tr = FakeTranslator()
    p, grab, _ = make(result("First line here."), tr)
    run(p.run(grab, Target("G", REGION)))
    p._ocr.res = result("Second line here.")
    run(p.run(grab, Target("G", REGION)))
    assert [c.source for c in tr.requests[1].context] == ["First line here."]


def test_no_text():
    p, grab, _ = make(result("|"))
    assert run(p.run(grab, Target("G", REGION))).kind == "no_text"


def test_translation_error_is_reported():
    p, grab, _ = make(result("Hello there."), FakeTranslator(fail="Нет связи"))
    out = run(p.run(grab, Target("G", REGION)))
    assert out.kind == "error" and out.message == "Нет связи"


def test_waits_for_typewriter_text():
    frames = [frame("I was"), frame("I was reluctant"), frame("I was reluctant to come here."), frame("I was reluctant to come here.")]
    p, grab, calls = make(result("I was reluctant to come here."), frames=frames)
    run(p.run(grab, Target("G", REGION)))
    assert calls["n"] == 4  # дождались двух одинаковых кадров


def test_capture_error():
    def bad():
        raise CaptureError("окно закрыто")

    p, _, _ = make(result("x"))
    out = run(p.run(bad, Target("G", REGION)))
    assert out.kind == "error" and "окно закрыто" in out.message


def test_request_carries_line_and_session_ids():
    tr = FakeTranslator()
    p, grab, _ = make(result("Hello there."), tr)
    out = run(p.run(grab, Target("G", REGION, game_exe="g.exe", session_id="sess-123456")))
    req = tr.requests[0]
    assert req.session_id == "sess-123456" and req.game_exe == "g.exe"
    assert req.line_id and req.line_id == out.record.line_id  # тот же id и в истории, и на сервере


def test_error_code_is_kept_for_the_app():
    class Limited(FakeTranslator):
        async def translate(self, req):
            raise TranslationError("Лимит переводов на сегодня закончился.", "limit_reached")

    p, grab, _ = make(result("Hello there."), Limited())
    out = run(p.run(grab, Target("G", REGION)))
    assert out.kind == "error" and out.code == "limit_reached"
    assert out.ocr is not None and out.utterance.text == "Hello there."  # F3 работает и без перевода


def test_scan_reads_words_without_translating():
    tr = FakeTranslator()
    p, grab, _ = make(result("Mira", "This place feels eerie."), tr)
    scan = run(p.scan(grab, Target("G", REGION)))
    assert scan.ok and scan.utterance.speaker == "Mira" and len(scan.ocr.words) == 5
    assert scan.record is None and tr.requests == []
    run(p.run(grab, Target("G", REGION)))
    again = run(p.scan(grab, Target("G", REGION)))
    assert again.record is not None and again.record.translation.startswith("RU:")


def test_scan_without_text():
    p, grab, _ = make(result("|"))
    assert not run(p.scan(grab, Target("G", REGION))).ok


def test_unexpected_translator_crash_is_a_readable_error():
    class Broken(FakeTranslator):
        async def translate(self, req):
            raise TypeError("create() got an unexpected keyword argument 'temperature'")

    p, grab, _ = make(result("Hello there."), Broken())
    out = run(p.run(grab, Target("G", REGION)))
    assert out.kind == "error" and "temperature" not in out.message


class ModeOcr:
    """OCR, который читает по-разному в зависимости от подготовки картинки."""

    def __init__(self, by_mode: dict[str, OcrResult]) -> None:
        self.by_mode = by_mode
        self.modes: list[str] = []

    async def recognize(self, prepared):
        # способ подготовки угадываем по картинке: pixel чёрно-белый, color/normal серые
        mode = getattr(prepared, "_mode", None)
        self.modes.append(mode)
        return self.by_mode[mode]


def test_auto_ocr_mode_picks_the_cleanest_reading(monkeypatch):
    import lexoplay.pipeline as pl

    real_prepare = pl.prepare

    class _Tagged:
        def __init__(self, prep, mode):
            self.prep, self._mode = prep, mode

    def tagged(frame_, region, *, mode="normal", binarize=False):
        return _Tagged(real_prepare(frame_, region, mode=mode), mode)

    monkeypatch.setattr(pl, "prepare", tagged)
    ocr = ModeOcr(
        {
            "normal": result("Y0u'r3 n0t |eaving th1s r00m"),
            "pixel": result("You're not leaving this room."),
            "color": result("You're n0t leav1ng"),
        }
    )
    p = Pipeline(ocr, FakeTranslator(), LineMemory(), sleep=no_sleep, run_sync=direct)
    out = run(p.run(lambda: frame("x"), Target("HM", REGION)))
    assert out.kind == "translated" and out.ocr_mode == "pixel"
    assert out.utterance.text == "You're not leaving this room."
    assert out.quality is not None and out.quality.ok
    assert out.preview is not None and out.preview.mode == "RGB"
    # F2 читает всеми способами; проверка смены реплики только уже выбранным
    ocr.modes.clear()
    reading = run(p.recognize(frame("x"), Target("HM", REGION, ocr_mode="pixel"), explore=False))
    assert ocr.modes == ["pixel"] and reading.mode == "pixel"


def test_disk_cache_serves_repeat_without_request(tmp_path):
    from lexoplay.history import LineCache
    from lexoplay.store import Store

    store = Store(tmp_path / "db.sqlite")
    cache = LineCache(store)
    tr = FakeTranslator()
    p1 = Pipeline(FakeOcr(result("I was reluctant to come here.")), tr, LineMemory(), sleep=no_sleep, run_sync=direct, cache=cache)
    t = Target("Midnight", REGION, game_exe="mid.exe", glossary={"Mira": "Мира"})
    out = run(p1.run(lambda: frame("x"), t))
    assert out.kind == "translated" and tr.requests[0].glossary == {"Mira": "Мира"}
    # новая сессия (память пустая), OCR чуть иначе: перевод из кеша, без запроса
    p2 = Pipeline(FakeOcr(result("I was reluctant to come here,")), tr, LineMemory(), sleep=no_sleep, run_sync=direct, cache=cache)
    out2 = run(p2.run(lambda: frame("x"), t))
    assert out2.kind == "repeat" and out2.cached and len(tr.requests) == 1
    assert out2.record.translation.startswith("RU:")
    assert store.lines_list("mid.exe")[0].hits == 1
    # другая игра: свой кеш
    p3 = Pipeline(FakeOcr(result("I was reluctant to come here,")), tr, LineMemory(), sleep=no_sleep, run_sync=direct, cache=cache)
    out3 = run(p3.run(lambda: frame("x"), Target("Other", REGION, game_exe="other.exe")))
    assert out3.kind == "translated" and len(tr.requests) == 2
    store.close()


def test_context_comes_from_game_history_in_a_new_session(tmp_path):
    from lexoplay.history import LineCache
    from lexoplay.store import Store

    store = Store(tmp_path / "db.sqlite")
    cache = LineCache(store)
    from lexoplay.translate import TRANSLATION_VERSION as V

    store.line_add("mid.exe", "ru", "old style", "Old Man", "Old style.", "Старый перевод.", 0)  # старая версия: не берём
    store.line_add("mid.exe", "ru", "who goes there", "Old Man", "Who goes there?", "Кто идёт?", V)
    store.line_add("mid.exe", "ru", "it s me mira", "Mira", "It's me, Mira.", "Это я, Мира.", V)
    store.line_add("other.exe", "ru", "unrelated", None, "Unrelated.", "Не относится.", V)
    tr = FakeTranslator()
    p = Pipeline(FakeOcr(result("Old Man", "Back again?")), tr, LineMemory(), sleep=no_sleep, run_sync=direct, cache=cache)
    out = run(p.run(lambda: frame("x"), Target("Midnight", REGION, game_exe="mid.exe")))
    assert out.kind == "translated"
    ctx = tr.requests[0].context
    assert [c.source for c in ctx] == ["Who goes there?", "It's me, Mira."]  # от старых к новым, только эта игра
    store.close()


def test_fusion_adds_words_highlighted_in_another_color():
    from lexoplay.ocrfuse import fuse

    def words(*items):
        return tuple(Word(t, Rect(x, y, w, 0.04)) for t, x, y, w in items)

    # голубой текст прочитан, жёлтые слова «Ultimate Biker Gang Leader» потерялись
    base = OcrResult(
        (
            Line(words(("Oh,", 0.06, 0.70, 0.04), ("and", 0.11, 0.70, 0.05), ("they", 0.17, 0.70, 0.06),
                       ("mentioned", 0.24, 0.70, 0.14), ("the", 0.39, 0.70, 0.05))),
            Line(words(("too.", 0.20, 0.78, 0.06))),
        )
    )
    other = OcrResult(
        (
            Line(words(("Ultimate", 0.46, 0.70, 0.12), ("Biker", 0.59, 0.70, 0.08), ("Gang", 0.68, 0.70, 0.07))),
            Line(words(("Leader", 0.06, 0.78, 0.12), ("t0o", 0.20, 0.78, 0.06))),
            Line(words(("|#", 0.5, 0.2, 0.02),)),  # мусор фона не добавляем
        )
    )
    fused, added = fuse(base, [other])
    assert added == 4
    assert fused.raw_text == "Oh, and they mentioned the Ultimate Biker Gang\nLeader too."
    same, none_added = fuse(base, [base])
    assert none_added == 0 and same is base


def test_waits_for_text_over_animated_scene():
    """Сцена за текстом шевелится (кадры не совпадают никогда): ждём, пока перестанет расти текст."""
    import random

    rnd = random.Random(1)

    def animated():
        img = Image.new("RGB", (800, 600), (rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255)))
        ImageDraw.Draw(img).rectangle((0, 400, 800, 600), fill=(rnd.randint(0, 255), 30, 30))
        return img

    texts = iter(
        ["Oh, and they", "Oh, and they mentioned the", "Oh, and they mentioned the Ultimate Biker Gang Leader too."]
        + ["Oh, and they mentioned the Ultimate Biker Gang Leader too."] * 50
    )

    class TypingOcr:
        async def recognize(self, prepared):
            return result(next(texts))

    tr = FakeTranslator()
    p = Pipeline(TypingOcr(), tr, LineMemory(), sleep=no_sleep, run_sync=direct)
    out = run(p.run(animated, Target("DR", REGION)))
    assert out.kind == "translated"
    assert tr.requests[0].text == "Oh, and they mentioned the Ultimate Biker Gang Leader too."


def test_wobbly_pixel_text_does_not_stall_translation():
    """Буквы «дышат» (Hotline Miami): кадры всегда разные, OCR чуть плавает, но текст не растёт."""
    import itertools
    import random

    rnd = random.Random(2)

    def animated():
        return Image.new("RGB", (800, 600), (rnd.randint(0, 255), 20, 40))

    reads = itertools.cycle(["YOU'RE NOT LEAVING THIS ROOM", "YOUIRE NOT LEAVING THIS R00M", "YOU'RE NOT LEAVlNG THIS ROOM"])
    calls = {"n": 0}

    class WobblyOcr:
        async def recognize(self, prepared):
            calls["n"] += 1
            return result(next(reads))

    p = Pipeline(WobblyOcr(), FakeTranslator(), LineMemory(), sleep=no_sleep, run_sync=direct)
    out = run(p.run(animated, Target("HM", REGION)))
    assert out.kind == "translated"
    assert calls["n"] <= 2 + 4  # два чтения на «перестал расти» и четыре способа для перевода


def test_old_version_translations_are_not_reused(tmp_path):
    from lexoplay.history import LineCache
    from lexoplay.store import Store

    store = Store(tmp_path / "db.sqlite")
    store.line_add("dr.exe", "ru", "my view became warped twisted", "Makoto", "My view became warped, twisted.",
                   "Мой... исказился, перекособочился,", 0)
    tr = FakeTranslator()
    p = Pipeline(FakeOcr(result("My view became warped, twisted.")), tr, LineMemory(), sleep=no_sleep, run_sync=direct,
                 cache=LineCache(store))
    out = run(p.run(lambda: frame("x"), Target("DR", REGION, game_exe="dr.exe")))
    assert out.kind == "translated" and len(tr.requests) == 1  # старый неудачный перевод переведён заново
    store.close()


def _page(*paras: tuple[str, float]) -> OcrResult:
    """Страница NVL: (текст абзаца, y) -> строки с рамками; абзацы разделены пустым местом."""
    lines = []
    for text, y in paras:
        words = text.split()
        ww = 0.7 / len(words)
        lines.append(Line(tuple(Word(w, Rect(0.1 + i * ww, y, ww * 0.9, 0.03)) for i, w in enumerate(words))))
    return OcrResult(tuple(lines))


PAGE = Rect(0.05, 0.05, 0.9, 0.85)


def test_page_translates_only_new_paragraphs():
    first = _page(("The morning was cold and grey.", 0.10), ("You're late, she said.", 0.20))
    p, grab, _ = make(first)
    seen: list[list] = []
    target = Target("Fate", PAGE, game_exe="fate.exe")
    out = run(p.run_page(grab, target, on_blocks=lambda bs: seen.append([b.record is not None for b in bs])))
    assert out.kind == "translated" and out.translated == 2
    assert [b.record.translation for b in out.blocks] == ["RU:The morning was cold and grey.", "RU:You're late, she said."]
    assert seen[0] == [False, False] and seen[-1] == [True, True]  # сначала точки, потом переводы
    assert out.blocks[0].rect.y == 0.10 and out.text.count("\n") == 1
    # игрок кликнул: на странице добавился абзац. Переводится только он, с прошлыми как контекстом
    p._ocr.res = _page(("The morning was cold and grey.", 0.10), ("You're late, she said.", 0.20), ("I nodded.", 0.30))
    tr = p.translator
    tr.requests.clear()
    out = run(p.run_page(grab, target))
    assert out.translated == 1 and [r.text for r in tr.requests] == ["I nodded."]
    ctx = [c.source for c in tr.requests[0].context]
    assert ctx[-2:] == ["The morning was cold and grey.", "You're late, she said."]
    assert all(b.record is not None for b in out.blocks)


def test_page_without_text_and_with_failed_paragraph():
    p, grab, _ = make(OcrResult())
    assert run(p.run_page(grab, Target("Fate", PAGE))).kind == "no_text"
    p, grab, _ = make(_page(("Only one paragraph here.", 0.1)), FakeTranslator(fail="лимит"))
    out = run(p.run_page(grab, Target("Fate", PAGE)))
    assert out.kind == "error" and out.message == "лимит" and out.blocks[0].error == "лимит"
