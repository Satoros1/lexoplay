"""Вкладки «История» и «Слова» и окно словаря игры.

История: все переведённые реплики, поиск, копирование. Удалённый перевод при следующем
F2 переведётся заново: так исправляется неудачный перевод.
Слова: сохранённые через F3. Без Lexo живут здесь и выгружаются в Anki или таблицу;
после привязки к Lexo сами уходят в карточки.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lexoplay.store import Game, SavedWord, Store, StoredLine
from lexoplay.ui import theme
from lexoplay.ui.theme import icon
from lexoplay.ui.widgets import button, card, clear, label, page_shell

PAGE = 100
MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


class Ctl(Protocol):
    store: Store

    def history(self, exe: str | None = None, query: str = "", limit: int = 100, offset: int = 0) -> list[StoredLine]: ...
    def delete_line(self, line_id: int) -> None: ...
    def clear_history(self, exe: str | None = None) -> None: ...
    def saved_words(self) -> list[SavedWord]: ...
    def delete_word(self, word_id: int) -> None: ...
    def export_words(self, path: str, fmt: str) -> int: ...
    def glossary(self, exe: str) -> dict[str, str]: ...
    def set_glossary(self, exe: str, term: str, translation: str) -> None: ...
    def delete_glossary(self, exe: str, term: str) -> None: ...
    def speakers(self, exe: str) -> list[str]: ...


def when(ts: float) -> str:
    d = datetime.fromtimestamp(ts)
    now = datetime.now()
    if d.date() == now.date():
        return d.strftime("%H:%M")
    if d.year == now.year:
        return f"{d.day} {MONTHS[d.month - 1]} {d:%H:%M}"
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def tool(icon_name: str, tip: str, on_click: Callable[[], None]) -> QToolButton:
    b = QToolButton()
    b.setIcon(icon(icon_name, theme.MUTED, 16))
    b.setIconSize(QSize(16, 16))
    b.setToolTip(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.clicked.connect(on_click)
    return b


def selectable(lbl):  # type: ignore[no-untyped-def]
    lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lbl


def copy_text(text: str) -> None:
    QGuiApplication.clipboard().setText(text)


# ------------------------------------------------------------------ История
class HistoryPage(QWidget):
    def __init__(self, ctl: Ctl) -> None:
        super().__init__()
        self.ctl = ctl
        self._shown = PAGE
        scroll, lay, actions = page_shell(
            "История",
            "Все переведённые реплики. Повтор той же реплики берётся отсюда: сразу и бесплатно. "
            "Удали неудачный перевод, и в следующий раз реплика переведётся заново.",
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.game = QComboBox()
        self.game.setMinimumContentsLength(16)
        self.game.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.game.activated.connect(lambda _i: self._reset())
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по тексту и переводу")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icon("search", theme.MUTED, 16), QLineEdit.ActionPosition.LeadingPosition)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self._reset)
        self.search.textChanged.connect(lambda _t: self._search_timer.start())
        bar.addWidget(self.game)
        bar.addWidget(self.search, 1)
        clear_btn = button(" Очистить", None, "trash")
        clear_btn.clicked.connect(self._clear)
        bar.addWidget(clear_btn)
        lay.addLayout(bar)

        self.count = label("", "muted")
        lay.addWidget(self.count)
        self.box, self.box_lay = card()
        self.box_lay.setContentsMargins(0, 4, 0, 4)
        self.box_lay.setSpacing(0)
        lay.addWidget(self.box)
        self.more = button("Показать ещё")
        self.more.clicked.connect(self._more)
        lay.addWidget(self.more, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addStretch(1)

    def _games(self) -> None:
        current = self.game.currentData()
        self.game.clear()
        self.game.addItem("Все игры", None)
        for g in self.ctl.store.list_games():
            self.game.addItem(g.title, g.exe)
        idx = self.game.findData(current)
        self.game.setCurrentIndex(max(0, idx))

    def refresh(self) -> None:
        self._games()
        self._render()

    def _reset(self) -> None:
        self._shown = PAGE
        self._render()

    def _more(self) -> None:
        self._shown += PAGE
        self._render()

    def _render(self) -> None:
        exe = self.game.currentData()
        query = self.search.text().strip()
        rows = self.ctl.history(exe, query, self._shown + 1, 0)
        has_more = len(rows) > self._shown
        rows = rows[: self._shown]
        clear(self.box_lay)
        titles = {g.exe: g.title for g in self.ctl.store.list_games()}
        total = self.ctl.store.lines_count(exe)
        self.count.setText(f"Реплик в истории: {total}" if total else "")
        if not rows:
            text = (
                "Ничего не нашлось."
                if query
                else "Здесь появятся переведённые реплики. В игре нажми F2 на реплике, и она окажется тут."
            )
            empty = QWidget()
            el = QVBoxLayout(empty)
            el.setContentsMargins(18, 14, 18, 14)
            el.addWidget(label(text, "muted", wrap=True))
            self.box_lay.addWidget(empty)
        for i, line in enumerate(rows):
            self.box_lay.addWidget(self._row(line, titles.get(line.exe, line.exe), show_game=exe is None, last=i == len(rows) - 1))
        self.more.setVisible(has_more)

    def _row(self, line: StoredLine, game: str, *, show_game: bool, last: bool) -> QFrame:
        row = QFrame()
        row.setObjectName("rowLast" if last else "row")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(18, 10, 10, 10)
        lay.setSpacing(10)
        col = QVBoxLayout()
        col.setSpacing(3)
        meta = []
        if show_game:
            meta.append(game)
        if line.speaker:
            meta.append(line.speaker)
        meta.append(when(line.seen_at))
        if line.hits:
            meta.append(f"повторов: {line.hits}")
        col.addWidget(label(" · ".join(meta), "version"))
        col.addWidget(selectable(label(line.source, "muted", wrap=True)))
        tr = selectable(label(line.translation, "title", wrap=True))
        col.addWidget(tr)
        lay.addLayout(col, 1)
        tools = QVBoxLayout()
        tools.setSpacing(4)
        tools.addWidget(tool("copy", "Скопировать перевод", lambda: copy_text(line.translation)))
        tools.addWidget(
            tool("trash", "Удалить: в следующий раз реплика переведётся заново", lambda: self._delete(line.id))
        )
        tools.addStretch(1)
        lay.addLayout(tools)
        return row

    def _delete(self, line_id: int) -> None:
        self.ctl.delete_line(line_id)
        self._render()

    def _clear(self) -> None:
        exe = self.game.currentData()
        what = f"историю игры «{self.game.currentText()}»" if exe else "всю историю"
        ok = QMessageBox.question(
            self,
            "LexoPlay",
            f"Удалить {what}? Реплики тогда будут переводиться заново.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ok == QMessageBox.StandardButton.Yes:
            self.ctl.clear_history(exe)
            self._reset()


# ------------------------------------------------------------------ Слова
class WordsPage(QWidget):
    def __init__(self, ctl: Ctl, linked: Callable[[], bool], hotkey: Callable[[], str]) -> None:
        super().__init__()
        self.ctl = ctl
        self._linked = linked
        self._hotkey = hotkey
        scroll, lay, actions = page_shell("Слова", "Слова, которые ты сохранил в играх.")
        self.export_btn = button(" Экспорт", None, "download")
        menu = QMenu(self.export_btn)
        menu.addAction("Для Anki (.txt)", lambda: self._export("anki"))
        menu.addAction("Таблица для Excel (.csv)", lambda: self._export("csv"))
        self.export_btn.setMenu(menu)
        actions.addWidget(self.export_btn)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self.note = label("", "muted", wrap=True)
        lay.addWidget(self.note)
        self.box, self.box_lay = card()
        self.box_lay.setContentsMargins(0, 4, 0, 4)
        self.box_lay.setSpacing(0)
        lay.addWidget(self.box)
        self.status = label("", "ok", wrap=True)
        self.status.hide()
        lay.addWidget(self.status)
        lay.addStretch(1)

    def refresh(self) -> None:
        words = self.ctl.saved_words()
        local = sum(1 for w in words if not w.lexo)
        if self._linked():
            text = "Слова уходят в карточки Lexo. Здесь копия: её можно выгрузить в Anki или таблицу."
            if local:
                text += f" Ещё не отправлено: {local}."
        else:
            text = (
                f"ПК не привязан к Lexo, поэтому слова хранятся здесь. В игре нажми {self._hotkey()}, кликни по слову "
                "и «+ в мои слова». Когда привяжешь Lexo, они сами станут карточками."
            )
        self.note.setText(text)
        self.export_btn.setEnabled(bool(words))
        clear(self.box_lay)
        if not words:
            empty = QWidget()
            el = QVBoxLayout(empty)
            el.setContentsMargins(18, 14, 18, 14)
            el.addWidget(label("Слов пока нет.", "muted"))
            self.box_lay.addWidget(empty)
        for i, w in enumerate(words):
            self.box_lay.addWidget(self._row(w, last=i == len(words) - 1))

    def _row(self, w: SavedWord, *, last: bool) -> QFrame:
        row = QFrame()
        row.setObjectName("rowLast" if last else "row")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(18, 10, 10, 10)
        lay.setSpacing(12)
        col = QVBoxLayout()
        col.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(selectable(label(w.word, "title")))
        extra = " · ".join(p for p in (f"/{w.ipa}/" if w.ipa else "", w.pos or "") if p)
        if extra:
            top.addWidget(label(extra, "muted"))
        top.addWidget(label("в Lexo" if w.lexo else "только здесь", "chip" if w.lexo else "chipGray"))
        top.addStretch(1)
        col.addLayout(top)
        if w.translation:
            col.addWidget(selectable(label(w.translation, wrap=True)))
        if w.sentence:
            col.addWidget(selectable(label(w.sentence, "muted", wrap=True)))
        col.addWidget(label(" · ".join(p for p in (w.game or "", when(w.created_at) if w.created_at else "") if p), "version"))
        lay.addLayout(col, 1)
        tools = QVBoxLayout()
        tools.addWidget(tool("copy", "Скопировать слово и перевод", lambda: copy_text(f"{w.word} - {w.translation or ''}")))
        tools.addWidget(tool("trash", "Удалить из списка", lambda: self._delete(w.id)))
        tools.addStretch(1)
        lay.addLayout(tools)
        return row

    def _delete(self, word_id: int) -> None:
        self.ctl.delete_word(word_id)
        self.refresh()

    def _export(self, fmt: str) -> None:
        ext, flt = (".txt", "Anki (*.txt)") if fmt == "anki" else (".csv", "CSV (*.csv)")
        default = str(Path.home() / "Desktop" / f"LexoPlay слова {time.strftime('%Y-%m-%d')}{ext}")
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить слова", default, flt)
        if not path:
            return
        try:
            n = self.ctl.export_words(path, fmt)
        except OSError as exc:
            self.status.setText(f"Не получилось сохранить: {exc}")
            self.status.setObjectName("error")
        else:
            hint = " В Anki: Файл → Импорт и выбери этот файл." if fmt == "anki" else ""
            self.status.setText(f"Сохранено слов: {n}. {path}.{hint}")
            self.status.setObjectName("ok")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.status.show()


# ------------------------------------------------------------------ Словарь игры
class GlossaryDialog(QDialog):
    def __init__(self, ctl: Ctl, game: Game, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctl, self.game = ctl, game
        self.setWindowTitle(f"Словарь игры: {game.title}")
        self.setMinimumWidth(560)
        self.setStyleSheet(theme.stylesheet())
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(12)
        lay.addWidget(label("Имена и термины", "h2"))
        lay.addWidget(
            label(
                "Как переводить имена персонажей, места и словечки этой игры. Модель будет держаться этих вариантов "
                "во всех репликах. Пример: Monokuma → Монокума.",
                "muted",
                wrap=True,
            )
        )
        self.box, self.box_lay = card()
        self.box_lay.setContentsMargins(0, 4, 0, 4)
        self.box_lay.setSpacing(0)
        lay.addWidget(self.box)

        add = QHBoxLayout()
        self.term = QLineEdit()
        self.term.setPlaceholderText("В игре (Monokuma)")
        self.tr = QLineEdit()
        self.tr.setPlaceholderText("Как переводить (Монокума)")
        self.tr.returnPressed.connect(self._add)
        self.term.returnPressed.connect(self.tr.setFocus)
        plus = button(" Добавить", "primary", "plus")
        plus.clicked.connect(self._add)
        add.addWidget(self.term, 1)
        add.addWidget(self.tr, 1)
        add.addWidget(plus)
        lay.addLayout(add)
        self.hints = QHBoxLayout()
        self.hints.setSpacing(6)
        lay.addLayout(self.hints)
        close = QPushButton("Готово")
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)
        self._render()

    def _render(self) -> None:
        clear(self.box_lay)
        items = self.ctl.glossary(self.game.exe)
        if not items:
            empty = QWidget()
            el = QVBoxLayout(empty)
            el.setContentsMargins(16, 10, 16, 10)
            el.addWidget(label("Пока пусто.", "muted"))
            self.box_lay.addWidget(empty)
        for i, (term, tr) in enumerate(items.items()):
            row = QFrame()
            row.setObjectName("rowLast" if i == len(items) - 1 else "row")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(16, 8, 8, 8)
            t = label(term, "title")
            t.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            rl.addWidget(t, 1)
            rl.addWidget(label("→", "muted"))
            v = label(tr)
            v.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            rl.addWidget(v, 1)
            rl.addWidget(tool("trash", "Убрать", lambda t=term: self._delete(t)))
            self.box_lay.addWidget(row)
        clear(self.hints)
        names = [n for n in self.ctl.speakers(self.game.exe) if n not in items][:6]
        if names:
            self.hints.addWidget(label("Встречались:", "muted"))
            for n in names:
                b = QPushButton(n)
                b.setObjectName("chipButton")
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.clicked.connect(lambda _c=False, name=n: (self.term.setText(name), self.tr.setFocus()))
                self.hints.addWidget(b)
            self.hints.addStretch(1)

    def _add(self) -> None:
        term, tr = self.term.text().strip(), self.tr.text().strip()
        if not term or not tr:
            (self.term if not term else self.tr).setFocus()
            return
        self.ctl.set_glossary(self.game.exe, term, tr)
        self.term.clear()
        self.tr.clear()
        self.term.setFocus()
        self._render()

    def _delete(self, term: str) -> None:
        self.ctl.delete_glossary(self.game.exe, term)
        self._render()
