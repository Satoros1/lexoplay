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
* совсем другой текст (игрок пропустил реплику, началась следующая) → «new» сразу;
* тот же, но заметно длиннее (реплика допечаталась после клика) → тоже «new»;
* текста нет (окно диалога закрылось) или он похож наполовину → «gone»/«new», если так
  два раза подряд: одна вспышка или переход экрана не уберёт перевод зря.

Без автоперевода и «new», и «gone» просто прячут перевод. С автопереводом «new»
сразу переводит следующую реплику, а после «gone» наблюдатель ждёт (waiting), когда
в окне диалога снова появится текст.

Первые секунды после показа перевода (settle) проверяем и без действий игрока, но
только одно: не допечаталась ли реплика («grown»). Тогда перевод обновится сам: так
бывает, когда F2 нажали, пока текст ещё печатался поверх анимированной сцены.
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
MIN_LETTERS = 3  # меньше букв в ожидании: шум рамки окна диалога, а не реплика


def grown(old: str, new: str) -> bool:
    """Реплика допечаталась: новый текст заметно длиннее, и добавились настоящие слова, а не мусор OCR."""
    if len(new) <= len(old) * 1.25 + 8:
        return False
    from lexoplay.ocrscore import token_score

    seen = set(old.split())
    extra = [w for w in new.split() if w not in seen]
    return sum(1 for w in extra if len(w) >= 2 and token_score(w)[1] >= 0.7) >= 2


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
        waiting: bool = False,
        settle_for: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.norm = normalize_text(text)
        self._settle_until = clock() + settle_for
        self.waiting = waiting  # перевода на экране нет: ждём, когда появится новая реплика
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
    def by_input(self) -> bool:
        """Проверка разбужена действием игрока (или наблюдатель без Raw Input)."""
        return not self._gated or self._clock() <= self._armed_until

    @property
    def armed(self) -> bool:
        return self.by_input or self._clock() < self._settle_until

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

    def check(self, text: str, thumb: Image.Image | None = None) -> str | None:
        """Новый текст окна диалога → None (та же реплика), "new" или "gone"."""
        now = normalize_text(text)
        letters = sum(1 for c in now if c.isalpha())
        if not self.by_input:
            # игрок ничего не нажимал: реагируем только на допечатавшуюся реплику
            if now and likeness(self.norm, now) >= SAME and grown(self.norm, now):
                self.last_score = likeness(self.norm, now)
                return "grown"
            return None
        if self.waiting:
            if letters < MIN_LETTERS:
                self.last_score = None
                if thumb is not None:
                    self.baseline = thumb
                return None
            score = likeness(self.norm, now) if self.norm else 0.0
            self.last_score = score
            if score >= DIFFERENT:
                if thumb is not None:
                    self.baseline = thumb
                return None  # тот же шум или та же реплика, что уже переводили
            return "new"
        if not now:
            self._strikes += 1
            return "gone" if self._strikes >= 2 else None
        score = likeness(self.norm, now)
        self.last_score = score
        if score >= SAME:
            self._strikes = 0
            if grown(self.norm, now):
                return "grown"
            if thumb is not None:
                self.baseline = thumb  # шевелится фон, а не текст: дальше сравниваем с этим кадром
            return None
        if score < DIFFERENT:
            return "new"
        self._strikes += 1
        return "new" if self._strikes >= 2 else None

    def line_changed(self, text: str, thumb: Image.Image | None = None) -> bool:
        """Новый текст окна диалога → True, если перевод пора убрать."""
        return self.check(text, thumb) is not None
