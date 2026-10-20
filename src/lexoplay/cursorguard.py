"""Режим слов: заметить, что курсор держит игра.

Многие игры держат системный курсор у себя: зажимают его в окне (ClipCursor), каждый кадр
возвращают в центр или читают мышь напрямую (DirectInput), а свой курсор рисуют сами.
Окно режима слов фокус у игры не забирает, поэтому игра курсор не отпускает, и выбрать
слово мышью нельзя. Признак простой: мышь двигают (сырые отсчёты Raw Input растут), а
курсор стоит на месте. Тогда App показывает подсказку про стрелки и, если игра не в
эксклюзивном полноэкранном режиме, на время режима слов забирает фокус: игра без фокуса
курсор отпускает.
"""

from __future__ import annotations

from collections.abc import Callable

LOCK_TRAVEL = 120  # столько сырых отсчётов мыши без сдвига курсора: держит игра
TOLERANCE = 3  # на столько пикселей курсор может «дрожать», пока игра возвращает его в центр


class CursorGuard:
    def __init__(
        self,
        pos: Callable[[], tuple[int, int]],
        travel: Callable[[], int],
        *,
        threshold: int = LOCK_TRAVEL,
        tolerance: int = TOLERANCE,
        counts: Callable[[tuple[int, int]], bool] = lambda _p: True,
    ) -> None:
        """counts(pos): считать ли эту точку. Курсор, упёртый в край экрана, тоже стоит, хотя мышь
        двигают, а вне окна игры его никто не держит: такие точки не считаем."""
        self._pos, self._travel = pos, travel
        self._threshold, self._tolerance = threshold, tolerance
        self._counts = counts
        self._base: tuple[tuple[int, int], int] = ((0, 0), 0)
        self.locked = False

    def reset(self) -> None:
        self._base = (self._pos(), self._travel())
        self.locked = False

    def tick(self) -> bool:
        """True один раз: только что стало видно, что курсор держит игра."""
        (x, y), t = self._pos(), self._travel()
        (bx, by), bt = self._base
        if abs(x - bx) + abs(y - by) > self._tolerance or t < bt or not self._counts((x, y)):
            self._base = ((x, y), t)  # курсор двигается сам (или упёрся в край экрана): всё в порядке
            return False
        if t - bt < self._threshold:
            return False  # мышь почти не трогали
        self._base = ((x, y), t)
        if self.locked:
            return False
        self.locked = True
        return True
