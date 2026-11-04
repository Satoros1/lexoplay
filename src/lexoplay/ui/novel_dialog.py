"""«Слова новеллы»: текст новеллы из папки игры, сложные слова и выражения из него, отправка в колоду на паузе.

Список общий на всю новеллу (без деления на части: так он ничего не выдаёт о сюжете), самые частые слова
сверху. Игрок снимает галочки со знакомых, остальные ложатся в колоду игры в Lexo на паузу: учить их он
начнёт в приложении, вкладка «Ждут», в порядке, в каком они встретятся в игре. Реплик игры здесь нет,
примеры к словам модель пишет свои, в модель уходят только слова.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import math

from lexoplay.novelwords import COUNTS, LEVELS, PORTION, Candidate, NovelView, plural
from lexoplay.store import Game
from lexoplay.ui import theme
from lexoplay.ui.widgets import button, label

ABOUT = (
    "LexoPlay находит текст новеллы в папке игры и отбирает сложные для тебя слова и выражения, которые "
    "в ней часто встречаются. Сними галочки со знакомых, остальные лягут в колоду игры на паузу: учить их "
    "начнёшь в приложении Lexo, вкладка «Ждут». Без спойлеров: список общий на всю новеллу, а примеры "
    "к словам модель пишет свои. В модель уходят только слова, текст игры остаётся у тебя."
)
TIMES = ("раз", "раза", "раз")
WORDS = ("слово", "слова", "слов")


def novel_view(ctl: Any, exe: str) -> NovelView | None:
    """Контроллер без слов новеллы (заглушки в тестах и самопроверке) даёт None."""
    fn = getattr(ctl, "novel_info", None)
    if fn is None:
        return None
    try:
        view = fn(exe)
    except Exception:  # окно не должно падать из-за подсчёта
        return None
    return view if isinstance(view, NovelView) else None


def card_text(view: NovelView | None) -> tuple[str, str]:
    """Строка для карточки игры: (текст, стиль)."""
    if view is None:
        return "", "muted"
    if view.state == "scanning":
        return "ищу текст новеллы и считаю слова…", "muted"
    if view.state == "ready":
        return view.summary, "muted"
    if view.state == "error" or not view.can_read:
        return view.error, "warn"
    return view.summary or "текст ещё не искали", "muted"


def readiness_text(view: NovelView) -> str:
    """«Акт 1: знаешь 87%, с колодой 95%» по строке на часть."""
    rows = []
    for title, have, deck in view.parts:
        row = f"{title}: знаешь {round(have * 100)}%"
        if deck - have >= 0.005:
            row += f", с колодой {round(deck * 100)}%"
        rows.append(row)
    return "\n".join(rows)


class NovelDialog(QDialog):
    POLL_MS = 700

    def __init__(self, ctl: Any, game: Game, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctl, self.game = ctl, game
        self._unchecked: set[str] = set()  # снятые галочки переживают смену фильтров
        self._shown: list[Candidate] = []
        self._all: list[Candidate] = []  # весь список при текущих уровне и пороге, по всем частям
        self._groups: dict[str, list[Candidate]] = {}  # «0:1» (часть 1, порция 2) или «all» -> слова
        self._last_key: tuple[object, ...] | None = None
        self.setWindowTitle(f"Слова новеллы: {game.title}")
        self.setMinimumSize(600, 720)
        self.setStyleSheet(theme.stylesheet())
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(10)
        lay.addWidget(label("Слова новеллы", "h2"))
        lay.addWidget(label(ABOUT, "muted", wrap=True))
        self.status = label("", "muted", wrap=True)
        lay.addWidget(self.status)
        row = QHBoxLayout()
        self.scan = button(" Найти текст", "primary", "scan")
        self.scan.clicked.connect(lambda: self._scan(None))
        self.folder = QPushButton("Указать папку игры…")
        self.folder.setToolTip("Если LexoPlay не знает, где лежит игра, или нашёл не ту папку")
        self.folder.clicked.connect(self._pick_folder)
        self.level_btn = QPushButton("Оценить ещё раз")
        self.level_btn.setToolTip("Модель ставит словам уровень A1–C2 по списку слов")
        self.level_btn.clicked.connect(self._level)
        row.addWidget(self.scan)
        row.addWidget(self.folder)
        row.addWidget(self.level_btn)
        row.addStretch(1)
        lay.addLayout(row)
        self.ready = label("", "version", wrap=True)
        lay.addWidget(self.ready)

        self.words_box = QWidget()
        box = QVBoxLayout(self.words_box)
        box.setContentsMargins(0, 6, 0, 0)
        box.setSpacing(8)
        self.part_row = QWidget()
        part_lay = QHBoxLayout(self.part_row)
        part_lay.setContentsMargins(0, 0, 0, 0)
        part_lay.addWidget(label("Слова к части", "muted"))
        self.part = QComboBox()
        self.part.setToolTip(
            "Слово попадает в ту часть, где встречается впервые: его надо знать с неё. "
            f"Большая часть делится на порции примерно по {PORTION} слов, по порядку появления."
        )
        self.part.currentIndexChanged.connect(lambda: self._fill(auto=False))
        part_lay.addWidget(self.part, 1)
        box.addWidget(self.part_row)
        filters = QHBoxLayout()
        filters.addWidget(label("Уровень от", "muted"))
        self.min_level = QComboBox()
        for lv in LEVELS[2:]:
            self.min_level.addItem(lv, lv)
        self.min_level.currentIndexChanged.connect(self._filters_changed)
        filters.addWidget(self.min_level)
        filters.addSpacing(12)
        filters.addWidget(label("встречается от", "muted"))
        self.min_count = QComboBox()
        for n in COUNTS:
            self.min_count.addItem(plural(n, TIMES), n)
        self.min_count.currentIndexChanged.connect(self._filters_changed)
        filters.addWidget(self.min_count)
        filters.addStretch(1)
        box.addLayout(filters)
        self.list_info = label("", "muted", wrap=True)
        box.addWidget(self.list_info)
        self.list = QListWidget()
        self.list.setObjectName("novelWords")
        self.list.itemChanged.connect(self._item_changed)
        box.addWidget(self.list, 1)
        toggles = QHBoxLayout()
        self.all_on = QPushButton("Отметить все")
        self.all_on.clicked.connect(lambda: self._check_all(True))
        self.all_off = QPushButton("Снять все")
        self.all_off.clicked.connect(lambda: self._check_all(False))
        toggles.addWidget(self.all_on)
        toggles.addWidget(self.all_off)
        toggles.addStretch(1)
        box.addLayout(toggles)
        lay.addWidget(self.words_box, 1)
        self.result = label("", "muted", wrap=True)
        lay.addWidget(self.result)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.send = button(" Отправить", "primary", "check")
        self.send.clicked.connect(self._send)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.reject)
        bottom.addWidget(self.send)
        bottom.addWidget(close)
        lay.addLayout(bottom)

        self._timer = QTimer(self)
        self._timer.setInterval(self.POLL_MS)
        self._timer.timeout.connect(self._load)
        self._defaults_set = False
        self._load()
        view = novel_view(self.ctl, game.exe)
        if view is not None and view.state == "none" and view.folder and view.can_read:
            self._scan(None)  # папка уже известна: искать текст сразу, без лишней кнопки
        elif view is not None and view.state == "ready" and not view.leveled and not view.busy:
            self._level()  # текст посчитан, а уровни ещё нет (или оценка прервалась)

    # ------------------------------------------------------------------
    def _load(self) -> None:
        view = novel_view(self.ctl, self.game.exe) or NovelView()
        busy = view.busy or view.state == "scanning"
        self.scan.setEnabled(not busy and view.can_read)
        self.scan.setText(" Пересчитать" if view.state == "ready" else " Найти текст")
        self.folder.setEnabled(not busy)
        self.level_btn.setVisible(view.state == "ready" and not view.leveled and not busy)
        if busy:
            self._timer.start()
        else:
            self._timer.stop()
        self.status.setText(self._status(view))
        self.ready.setText(readiness_text(view) if view.state == "ready" else "")
        self.ready.setVisible(view.state == "ready" and len(view.parts) > 1)
        show = view.state == "ready" and (view.leveled or bool(self._shown) or not view.busy)
        self.words_box.setVisible(show)
        self.send.setVisible(show)
        self.result.setText(view.progress or view.message)
        if view.state == "ready" and not self._defaults_set:
            self._set_defaults(view)
        key = (view.state, view.leveled, view.busy, view.summary)
        if key != self._last_key:  # список заново только когда поменялись данные, а не на каждый опрос
            self._last_key = key
            if view.state == "ready":
                self._fill()
        self._update_send(view)

    def _set_defaults(self, view: NovelView) -> None:
        self._defaults_set = True
        start = LEVELS[min(len(LEVELS) - 1, LEVELS.index(view.user_level) + 1)] if view.user_level in LEVELS else "B2"
        self.min_level.blockSignals(True)
        self.min_level.setCurrentIndex(max(0, self.min_level.findData(start)))
        self.min_level.blockSignals(False)
        fn = getattr(self.ctl, "novel_default_count", None)
        n = fn(self.game.exe, start) if fn is not None else COUNTS[1]
        self.min_count.blockSignals(True)
        self.min_count.setCurrentIndex(max(0, self.min_count.findData(n if isinstance(n, int) else COUNTS[1])))
        self.min_count.blockSignals(False)

    def _status(self, view: NovelView) -> str:
        if view.state == "scanning":
            return "Ищу текст новеллы и считаю слова… Большая новелла считается до минуты."
        if view.state == "ready":
            return " ".join(x for x in (view.summary + ".", view.note) if x)
        if view.state == "error" or not view.can_read:
            return f"Текст не достать. {view.error}"
        if not view.folder:
            return (
                "Папка игры пока неизвестна: запусти игру с LexoPlay хотя бы раз и нажми «Найти текст», "
                "или укажи папку сам."
            )
        return f"Папка игры: {view.folder}. {view.summary.capitalize()}." if view.summary else f"Папка игры: {view.folder}."

    def _fill(self, auto: bool = True) -> None:
        """auto: данные или фильтры поменялись. Если выбранная часть опустела (всё отправлено),
        переходим к следующей, где слова ещё есть."""
        if auto:
            fn = getattr(self.ctl, "novel_words", None)
            level, count = self.min_level.currentData(), self.min_count.currentData()
            words = fn(self.game.exe, level, count) if fn is not None and level and count else None
            self._all = list(words) if isinstance(words, list) else []
            self._fill_parts()
        key = self.part.currentData()
        if self.part_row.isHidden() or key is None:
            self._shown = list(self._all)
        else:
            self._shown = sorted(self._groups.get(str(key), []), key=lambda c: (-c.total, c.order))
        self.list.blockSignals(True)
        self.list.clear()
        for c in self._shown:
            kind = "выражение · " if c.phrase else ""
            item = QListWidgetItem(f"{c.text}    {kind}{c.level} · {plural(c.total, TIMES)}")
            item.setData(Qt.ItemDataRole.UserRole, c.text)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked if c.text in self._unchecked else Qt.CheckState.Checked)
            self.list.addItem(item)
        self.list.blockSignals(False)
        self._update_info()

    def _fill_parts(self) -> None:
        """«Акт 1 · 52 слова», «Акт 2, порция 1 из 3 · 60 слов», «Вся новелла · 320 слов».
        Слово в части, где встречается впервые; часть больше PORTION слов делится по порядку появления."""
        view = novel_view(self.ctl, self.game.exe)
        titles = [p[0] for p in view.parts] if view is not None and view.parts else ["Вся новелла"]
        options: list[tuple[str, str, list[Candidate]]] = []
        for i, title in enumerate(titles):
            items = sorted((c for c in self._all if c.part == i), key=lambda c: c.order)
            k = max(1, math.ceil(len(items) / PORTION))
            size = math.ceil(len(items) / k) if items else 0
            for j in range(k):
                chunk = items[j * size : (j + 1) * size]
                count = plural(len(chunk), WORDS)
                if k == 1:
                    text = f"{title} · {count}"
                elif len(titles) == 1:
                    text = f"Порция {j + 1} из {k} · {count}"
                else:
                    text = f"{title}, порция {j + 1} из {k} · {count}"
                options.append((f"{i}:{j}", text, chunk))
        if len(options) > 1:
            options.append(("all", f"Вся новелла · {plural(len(self._all), WORDS)}", list(self._all)))
        self._groups = {key: chunk for key, _t, chunk in options}
        self.part_row.setVisible(len(options) > 1)
        if len(options) <= 1:
            return
        keep = self.part.currentData()
        if keep not in self._groups or (keep != "all" and not self._groups[keep]):
            keep = next((key for key, _t, chunk in options if chunk), "all")  # первая порция, где слова ещё есть
        self.part.blockSignals(True)
        self.part.clear()
        for key, text, _chunk in options:
            self.part.addItem(text, key)
        self.part.setCurrentIndex(max(0, self.part.findData(keep)))
        self.part.blockSignals(False)

    def _update_info(self) -> None:
        if not self._shown:
            if self._all:
                self.list_info.setText("Здесь новых сложных слов нет: выбери другую часть.")
            else:
                self.list_info.setText("Слов с такими условиями нет: попробуй уровень пониже или порог поменьше.")
            return
        phrases = sum(c.phrase for c in self._shown)
        words = len(self._shown) - phrases
        what = plural(words, WORDS) + (f" и {plural(phrases, ('выражение', 'выражения', 'выражений'))}" if phrases else "")
        key = self.part.currentData()
        where = " впервые встречаются здесь" if not self.part_row.isHidden() and key not in (None, "all") else ""
        self.list_info.setText(f"{what}{where}. Сверху самые частые. Знакомые снимай стрелками и пробелом.")

    def _filters_changed(self) -> None:
        self._fill()
        view = novel_view(self.ctl, self.game.exe)
        if view is not None:
            self._update_send(view)

    def _item_changed(self, item: QListWidgetItem) -> None:
        word = str(item.data(Qt.ItemDataRole.UserRole))
        if item.checkState() == Qt.CheckState.Checked:
            self._unchecked.discard(word)
        else:
            self._unchecked.add(word)
        view = novel_view(self.ctl, self.game.exe)
        if view is not None:
            self._update_send(view)

    def _check_all(self, on: bool) -> None:
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def _chosen(self) -> tuple[list[str], list[str]]:
        chosen, skipped = [], []
        for i in range(self.list.count()):
            item = self.list.item(i)
            word = str(item.data(Qt.ItemDataRole.UserRole))
            (chosen if item.checkState() == Qt.CheckState.Checked else skipped).append(word)
        return chosen, skipped

    def _update_send(self, view: NovelView) -> None:
        chosen, skipped = self._chosen()
        if chosen:
            self.send.setText(f" Отправить {plural(len(chosen), WORDS)}")
        elif skipped:
            self.send.setText(" Отметить как знакомые")
        else:
            self.send.setText(" Отправить")
        self.send.setEnabled(not view.busy and bool(chosen or skipped))
        self.all_on.setEnabled(not view.busy)
        self.all_off.setEnabled(not view.busy)

    # ------------------------------------------------------------------
    def _scan(self, folder: str | None) -> None:
        self.result.setText("")
        fn = getattr(self.ctl, "scan_novel", None)
        if fn is None:
            return
        if not fn(self.game.exe, folder):
            self.status.setText("Папка игры неизвестна. Запусти игру с LexoPlay или укажи папку сам.")
            return
        self._load()

    def _pick_folder(self) -> None:
        view = novel_view(self.ctl, self.game.exe)
        start = view.folder if view is not None else ""
        folder = QFileDialog.getExistingDirectory(self, "Папка игры", start)
        if folder:
            self._scan(folder)

    def _level(self) -> None:
        fn = getattr(self.ctl, "level_novel", None)
        if fn is not None:
            fn(self.game.exe)
        self._load()

    def _send(self) -> None:
        fn = getattr(self.ctl, "send_novel_words", None)
        if fn is None:
            return
        chosen, skipped = self._chosen()
        if not chosen and not skipped:
            return
        fn(self.game.exe, chosen, skipped)
        self._unchecked -= set(skipped)  # они теперь «уже знаю» и из списка уйдут
        self._last_key = None
        self._load()
