"""Разбор реплики v2: смысл целиком, «дословно», только настоящие трудные выражения."""

import json

from lexoplay.lookup import (
    EXPLAIN_VERSION,
    MAX_ITEMS,
    Explanation,
    ExplainItem,
    build_explain_prompt,
    explanation_from_json,
    explanation_json,
    parse_explanation,
)


def test_prompt_asks_for_meaning_first_and_whole_expressions():
    p = build_explain_prompt(
        sentence="Don't jump to conclusions.", speaker="Kyoko", translation="Не спеши с выводами.",
        game="Danganronpa", context=[("Makoto", "It was him!")], target_lang="ru", source_lang="en",
    )
    assert '"meaning"' in p and '"literal"' in p and '"base"' in p
    assert "never a single everyday word" in p and f"0 to {MAX_ITEMS}" in p
    assert "Makoto: It was him!" in p and "(Kyoko)" in p and "—" not in p


def test_parse_v2_reply_and_keep_old_summary_working():
    raw = json.dumps({
        "meaning": "Кёко одёргивает: не делай выводов без улик — спокойно и холодно.",
        "literal": "Не прыгай к выводам.",
        "items": [
            {"phrase": "jump to conclusions", "base": "jump to conclusions", "meaning": "спешить с выводами", "note": "идиома"},
            {"phrase": "Look at", "base": "look at something", "meaning": "посмотреть на", "note": ""},
            {"phrase": "", "meaning": "пусто"},
            {"phrase": "a", "base": "", "meaning": "b"}, {"phrase": "c", "meaning": "d"},
        ],
    }, ensure_ascii=False)
    exp = parse_explanation("Вот JSON: " + raw)
    assert exp.summary.startswith("Кёко") and "—" not in exp.summary  # длинное тире убрано
    assert exp.literal == "Не прыгай к выводам."
    assert len(exp.items) == MAX_ITEMS
    assert exp.items[0].base == "" and exp.items[0].lemma == "jump to conclusions"  # та же форма: не повторяем
    assert exp.items[1].base == "look at something" and exp.items[1].lemma == "look at something"
    old = parse_explanation('{"items": [{"phrase": "after dark", "meaning": "после заката"}], "summary": "Тихо."}')
    assert old.summary == "Тихо." and old.items[0].phrase == "after dark"


def test_saved_explanations_are_versioned():
    exp = Explanation((ExplainItem("feels eerie", "кажется жутким", "feel + прил.", "feel eerie"),), "Тревожно.", "Ощущается жутко.")
    raw = explanation_json(exp)
    assert json.loads(raw)["v"] == EXPLAIN_VERSION
    assert explanation_from_json(raw) == exp
    old = '{"items": [{"phrase": "after dark", "meaning": "после заката"}], "summary": "Тихо."}'  # 0.6.3, без версии
    assert explanation_from_json(old) is None  # разберём заново новым промптом
