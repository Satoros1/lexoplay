"""Обёртка Windows OCR без Windows: замки движков (одновременные вызовы одного движка ломают OCR)."""

from lexoplay.win.ocr import WinOcr


def test_one_lock_per_engine():
    ocr = WinOcr()
    base, own = object(), object()
    ocr._engine = base
    # движок для слота не создался, слот читает общим: и замок должен быть общий
    assert ocr._lock_for(base, 0) is ocr._lock_for(base, 2)
    # свой движок у проверки смены реплики: своя очередь, F2 её не ждёт
    assert ocr._lock_for(own, 3) is ocr._lock_for(own, 3)
    assert ocr._lock_for(own, 3) is not ocr._lock_for(base, 0)
