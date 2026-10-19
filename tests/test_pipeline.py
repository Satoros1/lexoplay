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
