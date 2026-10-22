"""Вкладка «Слова», окно словаря игры и общие мелочи для вкладок.

История по сессиям живёт в history_page.py, прогресс в progress_page.py.
Слова: сохранённые через F3. Без Lexo живут здесь и выгружаются в Anki или таблицу;
после привязки к Lexo сами уходят в карточки.
"""

from __future__ import annotations

import html
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
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lexoplay.recall import word_spans
from lexoplay.store import Game, SavedWord, Store, StoredLine, fold
from lexoplay.ui import theme
from lexoplay.ui.explainbox import HIGHLIGHT, explain_box, row_tool
from lexoplay.ui.theme import icon
from lexoplay.ui.widgets import button, card, clear, fade_in, label, page_shell

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


# ------------------------------------------------------------------ Слова
def times(n: int) -> str:
    """3 -> «3 раза», 5 -> «5 раз», 21 -> «21 раз»."""
    tail = "раза" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "раз"
    return f"{n} {tail}"


WORDS_PAGE = 100  # «Показать ещё»
WORDS_CHUNK = 30  # столько строк строим сразу; остальные по мере прокрутки
FEED_STEP = 5  # при прокрутке достраиваем по столько строк за раз, между кадрами
FIRST_ROWS = 10  # столько строк вкладка показывает сразу
SORTS = (("new", "Сначала новые"), ("seen", "Чаще встречаются"), ("abc", "По алфавиту"))


def word_matches(w: SavedWord, query: str) -> bool:
    """Поиск по слову, переводу и примеру: по-английски и по-русски, без регистра, ё = е."""
    q = fold(query.strip())
    if not q:
        return True
    hay = " \n".join(
        x or "" for x in (w.word, w.selection, w.translation, w.sentence, w.sentence_translation, w.game)
    )
    return q in fold(hay)


def sort_words(words: list[SavedWord], how: str) -> list[SavedWord]:
    if how == "seen":
        return sorted(words, key=lambda w: (-w.seen, -w.created_at, -w.id))
    if how == "abc":
        return sorted(words, key=lambda w: (fold(w.word), -w.id))
    return sorted(words, key=lambda w: (-w.created_at, -w.id))


def marked_html(text: str, bold: list[tuple[int, int]], query: str = "") -> str:
    """Текст для QLabel: слово жирным и зелёным, совпадения с поиском подсвечены."""
    marks = [0] * (len(text) + 1)  # 1: жирное, 2: найденное
    for a, b in bold:
        for i in range(max(0, a), min(len(text), b)):
            marks[i] |= 1
    q = fold(query.strip())
    low = fold(text)
    if q and len(low) == len(text):
        i = 0
        while (j := low.find(q, i)) >= 0:
            for k in range(j, j + len(q)):
                marks[k] |= 2
            i = j + len(q)
    out: list[str] = []
    i = 0
    while i < len(text):
        m = marks[i]
        j = i
        while j < len(text) and marks[j] == m:
            j += 1
        chunk = html.escape(text[i:j])
        if m & 2:
            chunk = f'<span style="background-color:{HIGHLIGHT}">{chunk}</span>'
        if m & 1:
            chunk = f'<b style="color:{theme.GREEN_INK}">{chunk}</b>'
        out.append(chunk)
        i = j
    return "".join(out).replace("\n", "<br>")


def rich_label(text: str, name: str | None, *, wrap: bool = True) -> QLabel:
    lbl = label("", name, wrap=wrap)
    lbl.setTextFormat(Qt.TextFormat.RichText)
    lbl.setText(text)
    lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lbl


class WordsPage(QWidget):
    def __init__(self, ctl: Ctl, linked: Callable[[], bool], hotkey: Callable[[], str]) -> None:
        super().__init__()
        self.ctl = ctl
        self._linked = linked
        self._hotkey = hotkey
        self._words: list[SavedWord] = []
        self._visible: list[SavedWord] = []
        self._rendered = 0
        self._rows: dict[int, QFrame] = {}
        self._sig: tuple = ()
        self._feed = QTimer(self)
        self._feed.setInterval(0)
        self._feed.timeout.connect(self._feed_tick)
        self._feed_left = 0
        self.explain_open: set[int] = set()  # слова с раскрытым разбором примера
        self.explains: dict[int, object] = {}  # разбор (Explanation) или текст ошибки; пока грузится, ключа нет
        self.saved: set[tuple[int, int]] = set()  # (слово, пункт разбора), которые уже сохранены
        self.notes: dict[int, str] = {}
        self.can_explain = hasattr(ctl, "explain_history_line") and hasattr(ctl, "word_line")
        self.can_save = hasattr(ctl, "save_phrase") and hasattr(ctl, "word_line")
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
        self._scroll = scroll
        scroll.verticalScrollBar().valueChanged.connect(self._near_bottom)
        self.note = label("", "muted", wrap=True)
        lay.addWidget(self.note)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск: слово, перевод или пример, по-английски и по-русски")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icon("search", theme.MUTED, 16), QLineEdit.ActionPosition.LeadingPosition)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self._filter)
        self.search.textChanged.connect(lambda _t: self._search_timer.start())
        self.sort = QComboBox()
        for code, text in SORTS:
            self.sort.addItem(text, code)
        self.sort.activated.connect(lambda _i: self._filter())
        bar.addWidget(self.search, 1)
        bar.addWidget(self.sort)
        lay.addLayout(bar)
        self.summary = label("", "muted")
        lay.addWidget(self.summary)

        self.box, self.box_lay = card()
        self.box_lay.setContentsMargins(0, 4, 0, 4)
        self.box_lay.setSpacing(0)
        lay.addWidget(self.box)
        self.more = button("Показать ещё")
        self.more.clicked.connect(self._more)
        lay.addWidget(self.more, 0, Qt.AlignmentFlag.AlignHCenter)
        self.status = label("", "ok", wrap=True)
        self.status.hide()
        lay.addWidget(self.status)
        lay.addStretch(1)

    @property
    def query(self) -> str:
        return self.search.text().strip()

    def refresh(self) -> None:
        self._words = self.ctl.saved_words()
        local = sum(1 for w in self._words if not w.lexo)
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
        self.export_btn.setEnabled(bool(self._words))
        sig = tuple((w.id, w.seen, w.lexo, w.sentence) for w in self._words)
        if sig == self._sig and self._rows:
            return  # ничего не поменялось: список не строим заново, вкладка открывается сразу
        self._sig = sig
        self._render()

    def _filter(self) -> None:
        self._render()

    def _more(self) -> None:
        self._append(WORDS_PAGE)

    def _near_bottom(self, value: int) -> None:
        bar = self._scroll.verticalScrollBar()
        if value >= bar.maximum() - 1400 and self._rendered < len(self._visible) and not self._feed.isActive():
            # дочитываем список по мере прокрутки, по нескольку строк за раз: прокрутка не замирает
            self._feed_left = WORDS_CHUNK
            self._feed.start()

    def _feed_tick(self) -> None:
        n = min(FEED_STEP, self._feed_left)
        self._append(n)
        self._feed_left -= n
        if self._feed_left <= 0 or self._rendered >= len(self._visible):
            self._feed.stop()

    def visible_words(self) -> list[SavedWord]:
        found = [w for w in self._words if word_matches(w, self.query)]
        return sort_words(found, self.sort.currentData() or "new")

    def _render(self) -> None:
        self._feed.stop()
        clear(self.box_lay)
        self._rows = {}
        self._rendered = 0
        self._visible = words = self.visible_words()
        total = len(self._words)
        if self.query:
            self.summary.setText(f"Найдено: {len(words)} из {total}" if words else f"Ничего не нашлось среди {total} слов")
        else:
            self.summary.setText(f"Слов: {total}" if total else "")
        self.summary.setVisible(bool(total))
        self.search.setVisible(bool(total))
        self.sort.setVisible(bool(total))
        if not words:
            empty = QWidget()
            el = QVBoxLayout(empty)
            el.setContentsMargins(18, 14, 18, 14)
            el.addWidget(label("Слов пока нет." if not total else "Попробуй другое слово или перевод.", "muted"))
            self.box_lay.addWidget(empty)
        # первые строки сразу (их и видно), остальные между кадрами: вкладка открывается без задержки
        self._append(FIRST_ROWS)
        if self._rendered < len(words):
            self._feed_left = WORDS_CHUNK - FIRST_ROWS
            self._feed.start()

    def _append(self, n: int) -> None:
        part = self._visible[self._rendered : self._rendered + n]
        if not part:
            return
        if self._rows:  # прошлая последняя строка больше не последняя: ей нужна линия снизу
            last = self.box_lay.itemAt(self.box_lay.count() - 1).widget()
            if last is not None and last.objectName() == "rowLast":
                last.setObjectName("row")
                last.style().unpolish(last)
                last.style().polish(last)
        for w in part:
            self._rendered += 1
            row = self._row(w, last=self._rendered == len(self._visible))
            self._rows[w.id] = row
            self.box_lay.addWidget(row)
        self.more.setVisible(self._rendered < len(self._visible))

    def _redraw(self, w: SavedWord) -> None:
        """Перерисовать одну строку (раскрыли разбор): остальной список не трогаем."""
        old = self._rows.get(w.id)
        if old is None:
            return
        fresh = next((x for x in self._words if x.id == w.id), w)
        row = self._row(fresh, last=old.objectName() == "rowLast")
        self.box_lay.replaceWidget(old, row)
        old.hide()
        old.deleteLater()
        self._rows[w.id] = row
        fade_in(row)

    def _row(self, w: SavedWord, *, last: bool) -> QFrame:
        q = self.query
        row = QFrame()
        row.setObjectName("rowLast" if last else "row")
        col = QVBoxLayout(row)
        col.setContentsMargins(18, 10, 12, 10)
        col.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(8)
        extra = " · ".join(p for p in (f"/{w.ipa}/" if w.ipa else "", w.pos or "") if p)
        head = marked_html(w.word, [], q)
        if extra:
            head += f' <span style="color:{theme.MUTED}; font-size:10pt; font-weight:400">&nbsp;{html.escape(extra)}</span>'
        top.addWidget(rich_label(head, "title", wrap=False))
        top.addWidget(label("в Lexo" if w.lexo else "только здесь", "chip" if w.lexo else "chipGray"))
        top.addStretch(1)
        if self.can_explain and w.sentence:
            opened = w.id in self.explain_open
            top.addWidget(
                row_tool(
                    "lightbulb",
                    "Свернуть разбор" if opened else "Разбор примера: смысл, сленг, идиомы (как F7 в игре)",
                    lambda: self.toggle_explain(w),
                    theme.GREEN if opened else theme.MUTED,
                )
            )
        top.addWidget(row_tool("copy", "Скопировать слово и перевод", lambda: copy_text(f"{w.word} - {w.translation or ''}")))
        top.addWidget(row_tool("trash", "Удалить из списка", lambda: self._delete(w.id)))
        col.addLayout(top)
        # всё остальное одной надписью: так длинный список строится в разы быстрее
        parts: list[str] = []
        if w.translation:
            parts.append(f"<div>{marked_html(w.translation, [], q)}</div>")
        if w.sentence:
            example = marked_html(w.sentence, word_spans(w.sentence, w.selection, w.lemma), q)
            parts.append(f'<div style="color:{theme.MUTED}; margin-top:3px">{example}</div>')
            if w.sentence_translation:
                parts.append(
                    f'<div style="color:{theme.MUTED}; font-size:8.5pt; margin-top:1px">'
                    f"{marked_html(w.sentence_translation, [], q)}</div>"
                )
        meta = [w.game or "", when(w.created_at) if w.created_at else ""]
        if w.seen:
            meta.append(f"снова встретилось {times(w.seen)}")
        parts.append(
            f'<div style="color:{theme.MUTED}; font-size:8.5pt; margin-top:3px">'
            f'{html.escape(" · ".join(p for p in meta if p))}</div>'
        )
        col.addWidget(rich_label("".join(parts), None))
        if w.id in self.notes:
            col.addWidget(label(self.notes[w.id], "ok", wrap=True))
        if self.can_explain and w.id in self.explain_open:
            col.addSpacing(4)
            col.addWidget(
                explain_box(
                    self.explains.get(w.id),
                    saved=lambda i: (w.id, i) in self.saved,
                    on_save=(lambda i, item: self.save_phrase(w, i, item)) if self.can_save else None,
                )
            )
        return row

    def toggle_explain(self, w: SavedWord) -> None:
        if w.id in self.explain_open:
            self.explain_open.discard(w.id)
            self._redraw(w)
            return
        self.explain_open.add(w.id)
        if isinstance(self.explains.get(w.id), str):
            self.explains.pop(w.id)  # прошлая ошибка: пробуем ещё раз
        if w.id not in self.explains:

            def done(exp, err, word=w):  # type: ignore[no-untyped-def]
                self.explains[word.id] = exp if err is None else err
                self._redraw(word)

            self.ctl.explain_history_line(self.ctl.word_line(w), done)  # type: ignore[attr-defined]
        self._redraw(w)

    def save_phrase(self, w: SavedWord, index: int, item: object) -> None:
        line = self.ctl.word_line(w)  # type: ignore[attr-defined]
        where = self.ctl.save_phrase(line, item.phrase, item.meaning, item.base)  # type: ignore[attr-defined]
        self.saved.add((w.id, index))
        self.notes[w.id] = f"«{item.base or item.phrase}» сохранено в {where}"  # type: ignore[attr-defined]
        self._words = self.ctl.saved_words()
        self._redraw(w)  # новое слово появится в списке, когда вкладку откроют снова

    def _delete(self, word_id: int) -> None:
        """Убрать одну строку, не строя список заново."""
        self.ctl.delete_word(word_id)
        self._words = [w for w in self._words if w.id != word_id]
        self._visible = [w for w in self._visible if w.id != word_id]
        self._sig = tuple((w.id, w.seen, w.lexo, w.sentence) for w in self._words)
        row = self._rows.pop(word_id, None)
        if row is None:
            self._render()
            return
        self.box_lay.removeWidget(row)
        row.hide()
        row.deleteLater()
        self._rendered -= 1
        if not self._visible:
            self._render()
            return
        total = len(self._words)
        self.summary.setText(f"Найдено: {len(self._visible)} из {total}" if self.query else f"Слов: {total}")
        self.export_btn.setEnabled(bool(self._words))
        if self._rendered < len(self._visible):
            self._append(1)

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
