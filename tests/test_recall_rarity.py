"""Трудные слова (частотный список) и напоминание о сохранённых словах."""

from lexoplay.rarity import LEVELS, bases, rank, tier
from lexoplay.recall import FRESH_S, MAX_CHIPS, Recaller, RecallIndex, short_translation
from lexoplay.store import SavedWord, Store
from lexoplay.wordmode import game_names, reader_views

OLD = 1_700_000_000.0


def _w(word: str, tr: str, wid: int, *, lemma: str | None = None, sentence: str | None = None, at: float = OLD, seen: int = 0):
    return SavedWord("g.exe", "G", word, lemma, tr, sentence=sentence, created_at=at, id=wid, seen=seen)


def test_rank_uses_inflections_but_not_er():
    assert rank("talked") == rank("talk") < 1000
    assert rank("conclusions") == rank("conclusion")
    assert rank("bitter") != rank("bit")  # bitter не «bit + er»
    assert rank("eerie") is None and rank("Monokuma") is None
    assert "give" in bases("gave") and "bite" not in bases("bit")  # «a bit» не «укусил»


def test_tiers_by_level_and_junk():
    assert tier("despair", "b2") == "rare" and tier("dread", "c1") == "rare"
    assert tier("despair", "c1") == "rare" and tier("ultimate", "b2") == "" and tier("ultimate", "b1") == "rare"
    assert tier("evidence", "b2") == "common" and tier("the", "b2") == "common"
    assert tier("eerie,", "b2") == "rare"  # нет в списке: редкое
    for junk in ("Hmm...", "Ahhhh!", "hahaha", "uh", "y'know", "brr", "R2D2", "ok"):
        assert tier(junk, "b2") != "rare", junk
    assert tier("eerie", None) == "" and tier("eerie", "off") == ""
    assert tier("half-eerie", "b2") == "rare"
    assert set(LEVELS) == {"b1", "b2", "c1"}


def test_reader_views_mark_rare_words_but_not_names():
    names = game_names(
        ["Welcome to Hope's Peak Academy, Makoto.", "I hope you like it.", "Talk to Monokuma later."],
        speakers=["Kyoko Kirigiri"],
        terms={"Ultimate Lucky Student": "Абсолютный Счастливчик"},
    )
    assert {"Makoto", "Monokuma", "Kyoko", "Kirigiri", "Peak"} <= names
    assert "Hope" not in names and "Ultimate" not in names  # «hope» встречалось с маленькой, Ultimate есть в словаре
    views = reader_views("Monokuma laughed. Kyoko felt eerie dread, Makoto.", level="b2", names=names)
    tiers = {v.text.strip(".,"): v.tier for v in views}
    assert tiers["Monokuma"] == "name" and tiers["Kyoko"] == "name" and tiers["Makoto"] == "name"
    assert tiers["eerie"] == "rare" and tiers["dread"] == "rare"
    # без списка имён: с большой буквы посреди фразы тоже имя
    views = reader_views("I met Zorblax yesterday.", level="b2")
    assert [v.tier for v in views if v.text.startswith("Zorblax")] == ["name"]
    # всё капсом: регистр ничего не говорит, имя узнаём только из списка
    views = reader_views("SAYAKA, THIS IS EERIE.", level="b2", names={"Sayaka"})
    assert [v.tier for v in views][:1] == ["name"] and views[-1].tier == "rare"
    assert all(v.tier == "" for v in reader_views("This is eerie.", level=None))


def test_recall_finds_saved_words_forms_and_phrases():
    idx = RecallIndex(
        [
            _w("gave up", "сдаваться", 1, lemma="give up"),
            _w("give", "давать", 2),
            _w("seed", "семя", 3),
            _w("eerie", "жуткий, зловещий; странный", 4, sentence="This place is eerie."),
            _w("Eerie", "жуткий", 5, lemma="eerie"),
            _w("the", "артикль", 6),  # служебные не напоминаем
            _w("give sb a hand", "помочь", 7),  # с заполнителем не ищем
        ]
    )
    found = [e.word for e in idx.find("He gave up, I see seeds. It's eerie. Gives me chills.")]
    assert found == ["give up", "seed", "eerie", "give"]  # give внутри «gave up» не считается, а «Gives» да
    assert [e.word for e in idx.find("I see the sea")] == []  # seed не находится в see
    eerie = next(e for e in idx.entries if e.word == "eerie")
    assert eerie.ids == [4, 5] and eerie.translation == "жуткий"
    assert short_translation("очень-очень длинный перевод этого слова") .endswith("…")


def test_recaller_throttles_skips_source_and_counts():
    t = [OLD + 3600]
    idx = RecallIndex(
        [
            _w("eerie", "жуткий", 1, sentence="This place is eerie."),
            _w("dread", "страх", 2, at=OLD + 3600 - 10),  # только что сохранили
            _w("despair", "отчаяние", 3, seen=4),
        ]
    )
    r = Recaller(clock=lambda: t[0])
    chips, ids = r.pick(idx, "Despair and dread. So eerie.", commit=False)
    assert [c.word for c in chips] == ["despair", "eerie"] and ids == [3, 1]
    chips, ids = r.pick(idx, "Despair and dread. So eerie.")
    assert [(c.word, c.seen) for c in chips] == [("despair", 5), ("eerie", 1)] and ids == [3, 1]
    # та же реплика ещё раз (F2 повторно): подсказки те же, встреча не считается второй раз
    chips, ids = r.pick(idx, "Despair and dread. So eerie.")
    assert len(chips) == 2 and ids == []
    # новая реплика через минуту: встреча считается, но подсказку не повторяем
    t[0] += 60
    chips, ids = r.pick(idx, "Eerie silence.")
    assert chips == [] and ids == [1]
    # через 15 минут снова можно; dread уже не «только что»
    t[0] += 15 * 60 + FRESH_S
    chips, _ids = r.pick(idx, "Another eerie dread.")
    assert [(c.word, c.seen) for c in chips] == [("eerie", 3), ("dread", 1)]
    # та реплика, из которой слово сохраняли: не напоминаем
    t[0] += 3600
    assert r.pick(idx, "This place is eerie.") == ([], [])
    many = RecallIndex([_w(w, "x", i) for i, w in enumerate(["alpha", "bravo", "charlie", "delta", "echo"], 10)])
    chips, ids = Recaller(clock=lambda: t[0]).pick(many, "alpha bravo charlie delta echo")
    assert len(chips) == MAX_CHIPS and len(ids) == 5


def test_store_counts_word_encounters(tmp_path):
    st = Store(tmp_path / "s.db")
    wid = st.word_add(_w("eerie", "жуткий", 0))
    before = st.words_stamp()
    st.words_seen([wid], OLD + 5)
    st.words_seen([wid], OLD + 9)
    w = st.words_list()[0]
    assert w.seen == 2 and w.seen_at == OLD + 9 and st.words_stamp() == before
    st.word_add(_w("dread", "страх", 0))
    assert st.words_stamp() != before
    st.close()
    # старая база без колонок seen: открывается и дописывает их
    import sqlite3

    db = sqlite3.connect(tmp_path / "s.db")
    db.execute("CREATE TABLE w2 AS SELECT id, exe, game, selection, lemma, translation, ipa, pos, sentence, "
               "sentence_translation, created_at, lexo FROM words")
    db.execute("DROP TABLE words")
    db.execute("ALTER TABLE w2 RENAME TO words")
    db.commit()
    db.close()
    st = Store(tmp_path / "s.db")
    assert [x.seen for x in st.words_list()] == [0, 0]
    st.close()


def test_word_spans_for_bold_examples():
    from lexoplay.recall import word_spans

    def bold(sentence, selection, lemma=None):  # type: ignore[no-untyped-def]
        return [sentence[a:b] for a, b in word_spans(sentence, selection, lemma)]

    assert bold("The target had to be lured out.", "lured", "lure") == ["lured"]
    assert bold("He gave up on it.", "", "give up") == ["gave up"]
    assert bold("She wasn't a blameless victim.", "Blameless") == ["blameless"]
    assert bold("Don't jump to conclusions.", "jump to", "jump to conclusions") == ["jump to conclusions"]
    assert bold("Nothing here.", "eerie", "eerie") == []
