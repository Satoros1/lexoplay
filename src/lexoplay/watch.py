"""Когда прятать перевод: после действия игрока, и только если реплика сменилась.

Раньше хватало изменения пикселей в окне диалога. Но в играх там многое шевелится само:
мигает стрелка «дальше», мерцает рамка, буквы «дышат» и меняют цвет (Hotline Miami), и
перевод пропадал, хотя реплика та же. Теперь:

* проверка просыпается только после действия игрока: клик, колесо, клавиша (gated).
  Пока игрок ничего не нажимает, перевод не пропадает, что бы ни шевелилось на экране;
* после действия несколько секунд смотрим на пиксели, а решает текст: окно диалога
  распознаётся заново и сравнивается с переведённой репликой;
* похожесть считается и по буквам, и по словам: пиксельный шрифт OCR читает по-разному
  от кадра к кадру, но большая часть слов совпадает.

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
ARMED_FOR = 3.0  # сколько секунд после действия игрока ждём смены реплики


def _words(norm: str) -> set[str]:
    return {w for w in norm.split() if len(w) >= 4}


def likeness(a: str, b: str) -> float:
    """0..1: по буквам (difflib) или по словам, что выше. Нормализованные строки."""
    score = similarity(a, b)
    wa, wb = _words(a), _words(b)
    if wa and wb:
        score = max(score, len(wa & wb) / min(len(wa), len(wb)))
    return score


class LineWatcher:
    def __init__(
        self,
        text: str,
        baseline: Image.Image,
        *,
        pixel_threshold: float = 0.006,
        ocr_every: float = 0.45,
        gated: bool = False,
        armed_for: float = ARMED_FOR,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.norm = normalize_text(text)
        self._gated = gated
        self._armed_for = armed_for
        self._armed_until = float("-inf")
        self.acted = False  # игрок что-то нажимал с момента показа перевода
        self.baseline = baseline
        self._pixel_threshold = pixel_threshold
        self._ocr_every = ocr_every
        self._clock = clock
        self._last_ocr = float("-inf")
        self._strikes = 0
        self.checks = 0  # сколько раз перечитывали текст (для лога)
        self.last_score: float | None = None

    def arm(self) -> None:
        """Игрок кликнул или нажал клавишу: ближайшие секунды ждём смену реплики."""
        self.acted = True
        self._armed_until = self._clock() + self._armed_for

    @property
    def armed(self) -> bool:
        return not self._gated or self._clock() <= self._armed_until

    def needs_ocr(self, thumb: Image.Image) -> bool:
        """Игрок действовал и пиксели окна диалога сдвинулись: стоит перечитать текст."""
        if not self.armed:
            return False
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
        score = likeness(self.norm, now)
        self.last_score = score
        if score >= SAME:
            self._strikes = 0
            if thumb is not None:
                self.baseline = thumb  # шевелится фон, а не текст: дальше сравниваем с этим кадром
            return False
        if score < DIFFERENT:
            return True
        self._strikes += 1
        return self._strikes >= 2
