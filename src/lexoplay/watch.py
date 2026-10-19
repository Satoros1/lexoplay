"""Когда прятать перевод: только когда реплика в игре действительно сменилась.

Раньше хватало изменения пикселей в окне диалога. Но в играх там многое шевелится само:
мигает стрелка «дальше», мерцает рамка, по фону бегут цвета (Hotline Miami), и перевод
пропадал, хотя реплика та же. Теперь пиксели только будят проверку, а решает текст:
окно диалога распознаётся заново и сравнивается с переведённой репликой.

* текст тот же (OCR чуть плавает, похожесть ≥ 0.8) → перевод остаётся;
* совсем другой текст (игрок пропустил реплику, началась следующая) → прячем сразу;
* текста нет (окно диалога закрылось) или он похож наполовину → прячем, если так
  два раза подряд: одна вспышка или переход экрана не уберёт перевод зря.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from PIL import Image

from lexoplay.dedupe import normalize_text, similarity
from lexoplay.preprocess import changed

SAME = 0.8  # не ниже: та же реплика, OCR чуть плавает
DIFFERENT = 0.5  # ниже: точно другая реплика


class LineWatcher:
    def __init__(
        self,
        text: str,
        baseline: Image.Image,
        *,
        pixel_threshold: float = 0.006,
        ocr_every: float = 0.45,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.norm = normalize_text(text)
        self.baseline = baseline
        self._pixel_threshold = pixel_threshold
        self._ocr_every = ocr_every
        self._clock = clock
        self._last_ocr = float("-inf")
        self._strikes = 0
        self.checks = 0  # сколько раз перечитывали текст (для лога)

    def needs_ocr(self, thumb: Image.Image) -> bool:
        """Пиксели окна диалога сдвинулись настолько, что стоит перечитать текст."""
        if not changed(self.baseline, thumb, self._pixel_threshold):
            self._strikes = 0
            return False
        now = self._clock()
        if now - self._last_ocr < self._ocr_every:
            return False  # не чаще пары раз в секунду: OCR не бесплатный
        self._last_ocr = now
        self.checks += 1
        return True

    def line_changed(self, text: str, thumb: Image.Image | None = None) -> bool:
        """Новый текст окна диалога → True, если перевод пора убрать."""
        now = normalize_text(text)
        if not now:
            self._strikes += 1
            return self._strikes >= 2
        score = similarity(self.norm, now)
        if score >= SAME:
            self._strikes = 0
            if thumb is not None:
                self.baseline = thumb  # шевелится фон, а не текст: дальше сравниваем с этим кадром
            return False
        if score < DIFFERENT:
            return True
        self._strikes += 1
        return self._strikes >= 2
