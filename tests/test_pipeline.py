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
    # способ уже известен и читает хорошо: другие не пробуем
    ocr.modes.clear()
    out2 = run(p.run(lambda: frame("x"), Target("HM", REGION, ocr_mode="pixel")))
    assert ocr.modes == ["pixel"] and out2.kind == "repeat"


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
    store.line_add("mid.exe", "ru", "who goes there", "Old Man", "Who goes there?", "Кто идёт?")
    store.line_add("mid.exe", "ru", "it s me mira", "Mira", "It's me, Mira.", "Это я, Мира.")
    store.line_add("other.exe", "ru", "unrelated", None, "Unrelated.", "Не относится.")
    tr = FakeTranslator()
    p = Pipeline(FakeOcr(result("Old Man", "Back again?")), tr, LineMemory(), sleep=no_sleep, run_sync=direct, cache=cache)
    out = run(p.run(lambda: frame("x"), Target("Midnight", REGION, game_exe="mid.exe")))
    assert out.kind == "translated"
    ctx = tr.requests[0].context
    assert [c.source for c in ctx] == ["Who goes there?", "It's me, Mira."]  # от старых к новым, только эта игра
    store.close()
