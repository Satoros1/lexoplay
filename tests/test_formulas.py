"""Разговорные формулы: находятся в тексте новеллы и попадают в «Слова новеллы» как выражения."""

import json

from lexoplay import formulas, novel, novelwords
from lexoplay.lookup import EXPLAIN_PROMPT, SENSE_PROMPT


def _toks(text: str) -> list[tuple[str, bool]]:
    import re

    out = []
    for m in re.finditer(r"[A-Za-z]+(?:['’][A-Za-z]+)*", text):
        broken = bool(re.search(r"[.!?…,;:—()\"]", text[max(0, m.start() - 3) : m.start()])) or m.start() == 0
        out.append((m.group(0).lower().replace("’", "'"), broken))
    return out


def test_find_takes_the_longest_and_never_jumps_over_punctuation():
    assert formulas.find(_toks("No way, it turns out he was right.")) == [("no way", 0), ("it turns out", 2)]
    found = formulas.find(_toks("I can’t help but laugh. What's the point of it?"))
    assert found == [("i can't help but", 0), ("what's the point of", 5)]
    assert formulas.find(_toks("No, way.")) == []  # через запятую это уже не формула
    assert all(len(f.split()) >= 2 for f in formulas.FORMULAS)
    assert "end up" not in formulas.FORMULAS  # фразовые глаголы считает novelwords сам


def test_novel_counts_formulas_with_phrasal_verbs():
    lines = ["No way. I can't help but wonder.", "No way! We ended up here anyway."] * 4  # реже 4 раз не считаем
    sc = novel.Script(novel.Engine("renpy", "Ren'Py", True), [novel.Part("Вся новелла", lines)])
    parts, order = novelwords.count(sc)
    phrases = parts[0].phrases
    assert phrases["no way"] == 8 and phrases["i can't help but"] == 4 and phrases["end up"] == 4
    assert order["no way"] < order["i can't help but"]  # порядок появления: «no way» первая


def test_old_list_is_read_and_marked_for_recount(tmp_path, monkeypatch):
    monkeypatch.setattr(novelwords, "_dir", lambda: tmp_path)
    sc = novel.Script(novel.Engine("renpy", "Ren'Py", True), [novel.Part("Вся новелла", ["Hello there."])])
    nv = novelwords.build("vn.exe", "VN", tmp_path, sc)
    nv.taken, nv.skipped = {"reluctant"}, {"eerie"}
    novelwords.save(nv)
    path = novelwords._path("vn.exe")
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("features")  # список, разобранный прошлой версией
    path.write_text(json.dumps(data), encoding="utf-8")
    novelwords._CACHE.clear()
    old = novelwords.load("vn.exe")
    assert old is not None and old.features == 0 < novelwords.FEATURES  # пересчитается сам
    assert old.taken == {"reluctant"} and old.skipped == {"eerie"}  # «уже знаю» не теряется
    assert novelwords.build("vn.exe", "VN", tmp_path, sc).features == novelwords.FEATURES


def test_prompts_treat_conversational_formulas_as_expressions():
    assert "conversational formula" in SENSE_PROMPT and "I can't help but" in SENSE_PROMPT
    assert "conversational formulas" in EXPLAIN_PROMPT
