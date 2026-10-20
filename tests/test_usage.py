from datetime import date

from lexoplay.lookup import build_prompt, parse_sense
from lexoplay.store import Store
from lexoplay.usage import OwnUsage


def test_own_usage_counts_per_day(tmp_path):
    store = Store(tmp_path / "db")
    day = [date(2026, 10, 19)]
    u = OwnUsage(store, today=lambda: day[0])
    assert "ещё ничего" in u.summary("claude-haiku-5-5")
    u.add("line", (1500, 60))
    u.add("line", (1600, 70))
    u.add("cached")
    u.add("word", (300, 80))
    d = u.today()
    assert (d.lines, d.cached, d.words, d.tokens_in, d.tokens_out) == (2, 1, 1, 3400, 210)
    text = u.summary("claude-haiku-5-5")
    assert "реплик: 2" in text and "из кеша бесплатно: 1" in text and "меньше цента" in text
    assert "токенов" in u.summary("some-other-model") and "$" not in u.summary("some-other-model")
    day[0] = date(2026, 10, 20)
    assert u.today().lines == 0  # новый день: счёт заново
    store.close()


def test_lookup_prompt_and_parse():
    prompt = build_prompt('make "amends"', "I want to make amends.", "Я хочу загладить вину.", "ru", "en")
    assert "Russian" in prompt and "English" in prompt and "Sentence translation" in prompt
    sense = parse_sense(
        '```json\n{"lemma": "make amends", "pos": "phrase", "ipa": "/meɪk əˈmɛndz/", '
        '"senses": ["загладить вину — исправить"], "marked_translation": "Я хочу **загладить вину**."}\n```',
        "amends",
    )
    assert sense.lemma == "make amends" and sense.ipa == "meɪk əˈmɛndz"
    assert "—" not in sense.senses[0] and sense.translation.startswith("загладить вину")
