from lexoplay.dedupe import LineMemory, LineRecord, normalize_text, similarity


def rec(text: str, i: int = 0) -> LineRecord:
    return LineRecord(str(i), None, text, f"перевод {i}", normalize_text(text))


def test_normalize():
    assert normalize_text("  Wait... I'm  HERE!") == "wait i m here"


def test_similarity_ocr_noise():
    a = normalize_text("I was reluctant to come here tonight, you know.")
    b = normalize_text("l was reluctant to come here tonight you know")
    assert similarity(a, b) > 0.92


def test_memory_finds_repeat_with_noise():
    m = LineMemory()
    m.add(rec("I was reluctant to come here tonight, you know.", 1))
    m.add(rec("This place feels eerie after dark.", 2))
    found = m.find("l was reluctant to come here tonight you know")
    assert found is not None and found.line_id == "1"


def test_memory_misses_different_line():
    m = LineMemory()
    m.add(rec("I was reluctant to come here tonight.", 1))
    assert m.find("Sorry, the bus broke down halfway.") is None


def test_memory_limits_and_context_order():
    m = LineMemory(size=3)
    for i in range(5):
        m.add(rec(f"line number {i} with words", i))
    assert len(m) == 3
    assert [r.line_id for r in m.context(2)] == ["3", "4"]
