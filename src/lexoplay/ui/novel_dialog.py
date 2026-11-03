"""«Слова новеллы»: текст новеллы из папки игры, готовность к каждой части и колода из 10–15 слов перед ней.

Без спойлеров: части показываются только номерами («Акт 2»), реплик игры здесь нет совсем, а примеры
к словам модель пишет свои. Текст игры остаётся на компьютере, в модель уходят только слова.
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

from lexoplay.novelwords import NovelView, plural
from lexoplay.store import Game
from lexoplay.ui import theme
from lexoplay.ui.widgets import button, label

ABOUT = (
    "LexoPlay находит текст новеллы в папке игры и считает в нём слова. Перед каждой частью можно выучить "
    "10–15 слов, которые в ней встретятся чаще всего, и читать уже без словаря. Без спойлеров: части видны "
    "только номерами, а примеры к словам модель пишет свои, не из игры. Текст игры никуда не отправляется."
)
TIMES = ("раз", "раза", "раз")


def novel_view(ctl: Any, exe: str) -> NovelView | None:
    """Контроллер без слов новеллы (заглушки в тестах и самопроверке) даёт None."""
    fn = getattr(ctl, "novel_info", None)
    if fn is None:
        return None
    try:
        view = fn(exe)
    except Exception:
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


class NovelDialog(QDialog):
    POLL_MS = 600

    def __init__(self, ctl: Any, game: Game, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctl, self.game = ctl, game
        self.setWindowTitle(f"Слова новеллы: {game.title}")
        self.setMinimumSize(560, 600)
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
        row.addWidget(self.scan)
        row.addWidget(self.folder)
        row.addStretch(1)
        lay.addLayout(row)

        self.deck_box = QWidget()
        box = QVBoxLayout(self.deck_box)
        box.setContentsMargins(0, 6, 0, 0)
        box.setSpacing(8)
        part_row = QHBoxLayout()
        part_row.addWidget(label("Колода к части", "muted"))
        self.parts = QComboBox()
        self.parts.currentIndexChanged.connect(self._show_deck)
        part_row.addWidget(self.parts, 1)
        box.addLayout(part_row)
        self.deck_info = label("", "muted", wrap=True)
        box.addWidget(self.deck_info)
        self.list = QListWidget()
        self.list.setObjectName("novelWords")
        box.addWidget(self.list, 1)
        box.addWidget(label("Сними галочку со слов, которые уже знаешь: больше они не попадутся.", "version", wrap=True))
        self.result = label("", "muted", wrap=True)
        box.addWidget(self.result)
        lay.addWidget(self.deck_box, 1)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.add = button(" В колоду", "primary", "check")
        self.add.clicked.connect(self._add)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.reject)
        bottom.addWidget(self.add)
        bottom.addWidget(close)
        lay.addLayout(bottom)

        self._timer = QTimer(self)
        self._timer.setInterval(self.POLL_MS)
        self._timer.timeout.connect(self._load)
        self._busy = False
        self._load()

    # ------------------------------------------------------------------
    def _load(self) -> None:
        view = novel_view(self.ctl, self.game.exe) or NovelView()
        scanning = view.state == "scanning"
        self.scan.setEnabled(not scanning and view.can_read)
        self.scan.setText(" Пересчитать" if view.state == "ready" else " Найти текст")
        self.folder.setEnabled(not scanning)
        if scanning:
            self._timer.start()
        else:
            self._timer.stop()
        self.status.setText(self._status(view))
        ready = view.state == "ready" and bool(view.parts)
        self.deck_box.setVisible(ready)
        self.add.setVisible(ready)
        if ready:
            keep = max(0, self.parts.currentIndex())
            self.parts.blockSignals(True)
            self.parts.clear()
            for title, share in view.parts:
                self.parts.addItem(f"{title} · знаешь {round(share * 100)}% слов")
            self.parts.setCurrentIndex(min(keep, len(view.parts) - 1))
            self.parts.blockSignals(False)
            self._show_deck()

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

    def _show_deck(self) -> None:
        self.list.clear()
        fn = getattr(self.ctl, "novel_deck", None)
        deck = fn(self.game.exe, self.parts.currentIndex()) if fn is not None and self.parts.currentIndex() >= 0 else None
        if deck is None or not deck.words:
            self.deck_info.setText("Незнакомых слов, которые тут часто встречаются, нет: можно просто читать.")
            self.add.setEnabled(False)
            return
        self.deck_info.setText(
            f"Сейчас знаешь {round(deck.now * 100)}% слов этой части. "
            f"Эти {len(deck.words)} встретятся в ней {plural(deck.hits, TIMES)}, "
            f"с ними будет {round(deck.after * 100)}%."
        )
        for w in deck.words:
            item = QListWidgetItem(f"{w.lemma}  ·  здесь {plural(w.here, TIMES)}, до конца новеллы {w.total}")
            item.setData(Qt.ItemDataRole.UserRole, w.lemma)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.list.addItem(item)
        self.add.setEnabled(not self._busy)

    def _add(self) -> None:
        fn = getattr(self.ctl, "make_novel_deck", None)
        if fn is None or self._busy:
            return
        chosen, skipped = [], []
        for i in range(self.list.count()):
            item = self.list.item(i)
            word = str(item.data(Qt.ItemDataRole.UserRole))
            (chosen if item.checkState() == Qt.CheckState.Checked else skipped).append(word)
        self._busy = True
        self.add.setEnabled(False)
        self.result.setText("Модель пишет значения и примеры…" if chosen else "")
        fn(self.game.exe, chosen, skipped, self._added)

    def _added(self, ok: str | None, err: str | None) -> None:
        try:
            self._busy = False
            self.result.setText(ok or err or "")
            self._load()
        except RuntimeError:
            pass  # окно закрыли, пока модель отвечала
