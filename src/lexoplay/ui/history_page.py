"""Вкладка «История»: реплики по игровым сессиям.

Раньше это была одна длинная лента, и найти в ней что-то было почти нельзя. Теперь
реплики собраны в сессии (timeline.py): игра, когда, сколько длилась, сколько реплик и
слов. Сессия раскрывается по нажатию, внутри реплики по порядку, как сценарий. Поиск
показывает только сессии с совпадениями и сразу найденные реплики в них, с подсветкой;
«Вся сессия» открывает их в окружении. Звёздочка кладёт реплику в избранное.

У каждой реплики «Разбор» (то же, что F7 в игре: сленг, идиомы, трудные места, фразы
можно сохранить в слова; сохранённый разбор открывается сразу) и «Перевести иначе»
(моделью посильнее, с репликами перед этой; реплика остаётся в своей сессии).
Удалённый перевод при следующем F2 переведётся заново.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from datetime import date, datetime

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lexoplay.store import StoredLine
from lexoplay.timeline import (
    MONTHS_GEN,
    PlaySession,
    count,
    count_matches,
    day_title,
    duration,
    split_sessions,
    time_range,
)
from lexoplay.translate import drop_speaker_name
from lexoplay.ui import theme
from lexoplay.ui.explainbox import HIGHLIGHT, explain_box, row_tool
from lexoplay.ui.pages import copy_text
from lexoplay.ui.theme import icon
from lexoplay.ui.widgets import Tile, button, clear, fade_in, label, page_shell

SESSIONS_PAGE = 15  # сессий за раз, дальше «Показать ещё»
LINES_FIRST = 15  # последних реплик в только что раскрытой сессии
LINES_PAGE = 40  # и сколько добавляет «Показать раньше»
AUTO_OPEN = 3  # при поиске сразу раскрываем столько сессий с совпадениями
STAR = "#D99A1E"


def rich(text: str, query: str = "") -> str:
    """HTML реплики: совпадения с поиском подсвечены (без регистра, ё = е), переносы строк сохранены."""
    low = text.lower().replace("ё", "е")
    q = query.lower().replace("ё", "е")
    if not q or len(low) != len(text):
        return html.escape(text).replace("\n", "<br>")
    out, i = [], 0
    while (j := low.find(q, i)) >= 0:
        out.append(html.escape(text[i:j]))
        out.append(f'<span style="background-color:{HIGHLIGHT}">{html.escape(text[j : j + len(q)])}</span>')
        i = j + len(q)
    out.append(html.escape(text[i:]))
    return "".join(out).replace("\n", "<br>")


def text_label(text: str, name: str, query: str = "") -> QLabel:
    lbl = label("", name, wrap=True)
    lbl.setTextFormat(Qt.TextFormat.RichText)
    lbl.setText(rich(text, query))
    lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lbl


def session_text(title: str, s: PlaySession, lines: list[StoredLine]) -> str:
    """Сессия текстом для буфера обмена: как сценарий, оригинал и перевод под ним."""
    d = s.day
    head = f"{title}, {d.day} {MONTHS_GEN[d.month - 1]} {d.year}, {time_range(s)}"
    parts = [head]
    for line in lines:
        who = f"{line.speaker}: " if line.speaker else ""
        parts.append(f"{who}{line.source}\n{line.translation}")
    return "\n\n".join(parts)


class _Head(QFrame):
    """Шапка сессии: нажатие раскрывает и сворачивает."""

    def __init__(self, on_click: Callable[[], None]) -> None:
        super().__init__()
        self.setObjectName("sessionHead")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._on_click = on_click

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self._on_click()


class SessionCard(QFrame):
    def __init__(self, page: HistoryPage, s: PlaySession, title: str, *, opened: bool) -> None:
        super().__init__()
        self.page, self.s, self.title = page, s, title
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.head = _Head(self.toggle)
        hl = QHBoxLayout(self.head)
        hl.setContentsMargins(16, 12, 14, 12)
        hl.setSpacing(12)
        hl.addWidget(Tile(title, s.exe, 38), 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        top = QHBoxLayout()
        top.setSpacing(8)
        name = label(title, "title")
        top.addWidget(name, 1)
        top.addWidget(label(time_range(s), "muted"))
        col.addLayout(top)
        col.addWidget(label(self._meta(), "version"))
        self.preview = label("", "muted")
        self.preview.setTextFormat(Qt.TextFormat.PlainText)
        col.addWidget(self.preview)
        hl.addLayout(col, 1)
        self.chevron = QLabel()
        hl.addWidget(self.chevron, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.head)

        self.body = QFrame()
        self.body.setObjectName("sessionBody")
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(0, 2, 0, 10)
        self.body_lay.setSpacing(0)
        lay.addWidget(self.body)
        self.opened = False
        self.set_open(opened)

    # --- состояние, общее со страницей (переживает перерисовку списка)
    @property
    def limit(self) -> int:
        return self.page.limits.get(self.s.key, LINES_FIRST)

    @property
    def full(self) -> bool:
        """При поиске: показать всю сессию, а не только найденное."""
        return self.s.key in self.page.full

    def _meta(self) -> str:
        s = self.s
        parts = [duration(s.seconds)] if duration(s.seconds) else []
        parts.append(count(s.lines, "реплика", "реплики", "реплик"))
        if s.words:
            parts.append(count(s.words, "слово", "слова", "слов"))
        if s.matches and self.page.filtered:
            what = "в избранном" if self.page.starred_only and not self.page.query else "нашлось"
            parts.append(f"{what}: {s.matches}")
        return " · ".join(parts)

    def toggle(self) -> None:
        self.set_open(not self.opened)

    def set_open(self, on: bool) -> None:
        self.opened = on
        if on:
            self.page.opened.add(self.s.key)
        else:
            self.page.opened.discard(self.s.key)
        self.chevron.setPixmap(icon("chevron-up" if on else "chevron-down", theme.MUTED, 16).pixmap(16, 16))
        self.body.setVisible(on)
        self.preview.setVisible(not on)
        if on:
            self.render_body()
            if self.isVisible():
                fade_in(self.body)  # раскрылась плавно, а не выскочила
        elif not self.preview.text():
            last, _n = self._lines(1)
            self.preview.setText(self._preview(last[-1]) if last else "")

    @staticmethod
    def _preview(line: StoredLine) -> str:
        text = " ".join(line.translation.split())
        return text if len(text) <= 110 else text[:108].rstrip() + "…"

    def _lines(self, limit: int) -> tuple[list[StoredLine], int]:
        page = self.page
        only_found = page.filtered and not self.full
        return page.store.lines_range(
            self.s.exe,
            self.s.start,
            self.s.end,
            query=page.query if only_found else "",
            starred=page.starred_only if only_found else False,
            limit=limit,
        )

    def render_body(self) -> None:
        clear(self.body_lay)
        page = self.page
        lines, total = self._lines(self.limit)
        only_found = page.filtered and not self.full
        explained = page.explained(self.s.exe, lines)
        if not only_found:
            words = page.store.words_range(self.s.exe, self.s.start, self.s.end)
            if words:
                names = " · ".join(f"<b>{html.escape(w.word)}</b>" for w in words[:30])
                more = f" и ещё {len(words) - 30}" if len(words) > 30 else ""
                saved = label("", "muted", wrap=True)
                saved.setTextFormat(Qt.TextFormat.RichText)
                saved.setText(f"Сохранено в этой сессии: {names}{more}")
                saved.setContentsMargins(18, 10, 18, 4)
                self.body_lay.addWidget(saved)
        if total > len(lines):
            earlier = button(f"Показать раньше ({total - len(lines)})")
            earlier.setObjectName("link")
            earlier.clicked.connect(self._earlier)
            box = QHBoxLayout()
            box.setContentsMargins(18, 8, 18, 2)
            box.addWidget(earlier)
            box.addStretch(1)
            self.body_lay.addLayout(box)
        query = page.query if only_found or page.query else ""
        for i, line in enumerate(lines):
            self.body_lay.addWidget(self._row(line, query, last=i == len(lines) - 1, explained=line.norm in explained))
        foot = QHBoxLayout()
        foot.setContentsMargins(18, 8, 18, 0)
        foot.setSpacing(8)
        copy = button(" Скопировать сессию", None, "copy")
        copy.setToolTip("Все реплики сессии с переводом, как сценарий: удобно вставить в заметки")
        copy.clicked.connect(lambda: self._copy(copy))
        foot.addWidget(copy)
        if page.filtered:
            whole = button("Только найденное" if self.full else "Вся сессия")
            whole.clicked.connect(self._toggle_full)
            foot.addWidget(whole)
        foot.addStretch(1)
        self.body_lay.addLayout(foot)

    def _row(self, line: StoredLine, query: str, *, last: bool, explained: bool = False) -> QFrame:
        page = self.page
        row = QFrame()
        row.setObjectName("rowLast" if last else "row")
        lay = QVBoxLayout(row)
        lay.setContentsMargins(18, 8, 12, 9)
        lay.setSpacing(3)
        meta = QHBoxLayout()
        meta.setSpacing(2)
        bits = [line.speaker] if line.speaker else []
        bits.append(f"{datetime.fromtimestamp(line.seen_at):%H:%M}")
        if line.hits:
            bits.append(f"повторов: {line.hits}")
        meta.addWidget(label(" · ".join(bits), "version"), 1)
        starred = bool(line.star)
        meta.addWidget(
            row_tool(
                "star",
                "Убрать из избранного" if starred else "В избранное",
                lambda: self.page.star_line(line, not starred),
                STAR if starred else theme.MUTED,
                STAR if starred else "none",
            )
        )
        if page.can_explain:
            opened = line.id in page.explain_open
            meta.addWidget(
                row_tool(
                    "lightbulb",
                    "Свернуть разбор" if opened else ("Разбор (уже сохранён)" if explained else "Разбор: сленг, идиомы, трудные места"),
                    lambda: page.toggle_explain(line),
                    theme.GREEN if explained or opened else theme.MUTED,
                )
            )
        if page.can_retranslate:
            again = row_tool("refresh", "Перевести иначе: моделью посильнее, с репликами перед этой", lambda: page.retranslate(line))
            again.setEnabled(line.id not in page.busy)
            meta.addWidget(again)
        meta.addWidget(row_tool("copy", "Скопировать перевод", lambda: copy_text(line.translation)))
        meta.addWidget(
            row_tool("trash", "Удалить перевод: в следующий раз реплика переведётся заново", lambda: page.delete_line(line.id))
        )
        lay.addLayout(meta)
        lay.addWidget(text_label(line.source, "muted", query))
        if line.id in page.busy:
            lay.addWidget(label("Перевожу заново…", "muted"))
        else:
            lay.addWidget(text_label(drop_speaker_name(line.translation, line.speaker), "title", query))
        note = page.notes.get(line.id)
        if note:
            lay.addWidget(label(note[1], "ok" if note[0] else "error", wrap=True))
        if page.can_explain and line.id in page.explain_open:
            lay.addWidget(self._explain_panel(line))
        return row

    def _explain_panel(self, line: StoredLine) -> QWidget:
        page = self.page
        return explain_box(
            page.explains.get(line.id),
            saved=lambda i: (line.id, i) in page.saved,
            on_save=(lambda i, item: page.save_phrase(line, i, item.phrase, item.meaning, item.base)) if page.can_save else None,
        )

    def _earlier(self) -> None:
        self.page.limits[self.s.key] = self.limit + LINES_PAGE
        self.render_body()

    def _toggle_full(self) -> None:
        if self.full:
            self.page.full.discard(self.s.key)
        else:
            self.page.full.add(self.s.key)
        self.page.limits.pop(self.s.key, None)
        self.render_body()

    def _copy(self, btn: QPushButton) -> None:
        lines, _n = self.page.store.lines_range(self.s.exe, self.s.start, self.s.end, limit=100_000)
        copy_text(session_text(self.title, self.s, lines))
        btn.setText(" Скопировано")
        # кнопку могли уже убрать (сессию перерисовали): таймер с привязкой к ней тогда просто не сработает
        QTimer.singleShot(1500, btn, lambda: btn.setText(" Скопировать сессию"))


class HistoryPage(QWidget):
    def __init__(self, ctl) -> None:  # type: ignore[no-untyped-def]
        super().__init__()
        self.ctl = ctl
        self.store = ctl.store
        self.shown = SESSIONS_PAGE
        self.opened: set[str] = set()  # раскрытые сессии
        self.limits: dict[str, int] = {}  # сколько реплик показано в сессии
        self.full: set[str] = set()  # при поиске открыты целиком
        self.cards: list[SessionCard] = []
        self.busy: set[int] = set()  # реплики, которые сейчас переводятся заново
        self.notes: dict[int, tuple[bool, str]] = {}  # итог «Перевести иначе» под репликой: (удачно, текст)
        self.explain_open: set[int] = set()  # реплики с раскрытым разбором
        self.explains: dict[int, object] = {}  # разбор (Explanation) или текст ошибки; пока грузится, ключа нет
        self.saved: set[tuple[int, int]] = set()  # (реплика, пункт разбора), которые уже в словах
        # что умеет контроллер (в тестах и selftest он упрощённый)
        self.can_explain = hasattr(ctl, "explain_history_line")
        self.can_retranslate = hasattr(ctl, "retranslate_history_line")
        self.can_save = hasattr(ctl, "save_phrase")
        scroll, lay, _actions = page_shell(
            "История",
            "Реплики по игровым сессиям: нажми на сессию, чтобы открыть её. У каждой реплики есть разбор "
            "(лампочка), «Перевести иначе» (стрелки) и звёздочка, чтобы добавить её в избранное.",
        )
        self.scroll = scroll
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.game = QComboBox()
        self.game.setMinimumContentsLength(14)
        self.game.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.game.activated.connect(lambda _i: self._reset())
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по репликам, переводам и именам")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icon("search", theme.MUTED, 16), QLineEdit.ActionPosition.LeadingPosition)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self._reset)
        self.search.textChanged.connect(lambda _t: self._search_timer.start())
        self.star_btn = QPushButton(" Избранное")
        self.star_btn.setObjectName("filterChip")
        self.star_btn.setCheckable(True)
        self.star_btn.setIcon(icon("star", STAR, 16, fill=STAR))
        self.star_btn.setIconSize(QSize(16, 16))
        self.star_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.star_btn.setToolTip("Только реплики со звёздочкой")
        self.star_btn.toggled.connect(lambda _on: self._reset())
        clear_btn = QToolButton()
        clear_btn.setIcon(icon("trash", theme.MUTED, 16))
        clear_btn.setIconSize(QSize(16, 16))
        clear_btn.setToolTip("Очистить историю")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(self._clear)
        bar.addWidget(self.game)
        bar.addWidget(self.search, 1)
        bar.addWidget(self.star_btn)
        bar.addWidget(clear_btn)
        lay.addLayout(bar)

        self.summary = label("", "muted")
        lay.addWidget(self.summary)
        self.list_lay = QVBoxLayout()
        self.list_lay.setSpacing(10)
        lay.addLayout(self.list_lay)
        self.more = button("Показать ещё сессии")
        self.more.clicked.connect(self._more)
        lay.addWidget(self.more, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addStretch(1)

    # --- фильтр
    @property
    def query(self) -> str:
        return self.search.text().strip()

    @property
    def starred_only(self) -> bool:
        return self.star_btn.isChecked()

    @property
    def filtered(self) -> bool:
        return bool(self.query or self.starred_only)

    def _games(self) -> None:
        current = self.game.currentData()
        self.game.clear()
        self.game.addItem("Все игры", None)
        for g in self.store.list_games():
            self.game.addItem(g.title, g.exe)
        idx = self.game.findData(current)
        self.game.setCurrentIndex(max(0, idx))

    def refresh(self) -> None:
        self._games()
        self._reset()

    def _reset(self) -> None:
        """Фильтр сменился: заново, первые сессии раскрыты."""
        self.shown = SESSIONS_PAGE
        self.opened.clear()
        self.limits.clear()
        self.full.clear()
        self.notes.clear()
        self.explain_open.clear()
        self._render(auto_open=True)

    def _more(self) -> None:
        self.shown += SESSIONS_PAGE
        self._render()

    def _render(self, *, auto_open: bool = False) -> None:
        exe = self.game.currentData()
        sessions = split_sessions(self.store.history_events(exe))
        total_lines = sum(s.lines for s in sessions)
        if self.filtered:
            sessions = count_matches(sessions, self.store.lines_matching(exe, self.query, self.starred_only))
        found = sum(s.matches for s in sessions)
        if self.query:
            self.summary.setText(
                f"Нашлось {count(found, 'реплика', 'реплики', 'реплик')} "
                f"в {count(len(sessions), 'сессии', 'сессиях', 'сессиях')}"
                if found
                else ""
            )
        elif self.starred_only:
            self.summary.setText(f"В избранном {count(found, 'реплика', 'реплики', 'реплик')}" if found else "")
        else:
            self.summary.setText(
                f"{count(len(sessions), 'сессия', 'сессии', 'сессий')} · {count(total_lines, 'реплика', 'реплики', 'реплик')}"
                if sessions
                else ""
            )
        clear(self.list_lay)
        self.cards = []
        if not sessions:
            self.list_lay.addWidget(self._empty())
        titles = {g.exe: g.title for g in self.store.list_games()}
        today = date.today()
        prev_day: date | None = None
        open_first = AUTO_OPEN if self.filtered else 1
        for i, s in enumerate(sessions[: self.shown]):
            if s.day != prev_day:
                prev_day = s.day
                head = label(day_title(s.day, today).upper(), "section")
                head.setContentsMargins(2, 6 if i else 0, 0, 0)
                self.list_lay.addWidget(head)
            opened = s.key in self.opened or (auto_open and i < open_first)
            card = SessionCard(self, s, titles.get(s.exe, s.exe), opened=opened)
            self.cards.append(card)
            self.list_lay.addWidget(card)
        self.more.setVisible(len(sessions) > self.shown)

    def _empty(self) -> QWidget:
        if self.query:
            text = "Ничего не нашлось. Поиск идёт по оригиналу, переводу и именам говорящих."
        elif self.starred_only:
            text = "В избранном пока пусто. Нажми звёздочку у реплики, и она окажется здесь."
        else:
            text = "Здесь появятся переведённые реплики, по игровым сессиям. В игре нажми F2 на реплике."
        box, lay = card_box()
        lay.addWidget(label(text, "muted", wrap=True))
        return box

    # --- действия с репликами
    def explained(self, exe: str, lines: list[StoredLine]) -> set[str]:
        lang = getattr(getattr(self.ctl, "settings", None), "target_lang", "ru")
        try:
            return self.store.explained(exe, lang, [x.norm for x in lines])
        except Exception:
            return set()

    def toggle_explain(self, line: StoredLine) -> None:
        if line.id in self.explain_open:
            self.explain_open.discard(line.id)
            self._rerender_cards()
            return
        self.explain_open.add(line.id)
        if isinstance(self.explains.get(line.id), str):
            self.explains.pop(line.id)  # прошлая ошибка: пробуем ещё раз
        if line.id not in self.explains:

            def done(exp, err, line_id=line.id):  # type: ignore[no-untyped-def]
                self.explains[line_id] = exp if err is None else err
                self._rerender_cards()

            self.ctl.explain_history_line(line, done)
        self._rerender_cards()

    def retranslate(self, line: StoredLine) -> None:
        if line.id in self.busy:
            return
        self.busy.add(line.id)
        self.notes.pop(line.id, None)

        def done(text, err, line_id=line.id):  # type: ignore[no-untyped-def]
            self.busy.discard(line_id)
            self.notes[line_id] = (True, "Переведено заново") if err is None else (False, err)
            if err is None:  # старый разбор говорил о прошлом переводе: свернуть, новый по кнопке
                self.explains.pop(line_id, None)
                self.explain_open.discard(line_id)
            self._rerender_cards()

        self.ctl.retranslate_history_line(line, done)
        self._rerender_cards()

    def save_phrase(self, line: StoredLine, index: int, phrase: str, meaning: str, base: str = "") -> None:
        where = self.ctl.save_phrase(line, phrase, meaning, base)
        self.saved.add((line.id, index))
        self.notes[line.id] = (True, f"«{base or phrase}» сохранено в {where}")
        self._rerender_cards()

    def star_line(self, line: StoredLine, on: bool) -> None:
        self.store.line_star(line.id, on)
        self._rerender_cards()

    def delete_line(self, line_id: int) -> None:
        self.ctl.delete_line(line_id)
        self._render()

    def _rerender_cards(self) -> None:
        if self.starred_only:
            self._render()  # звёздочку сняли: реплика уходит из избранного
            return
        for c in self.cards:
            if c.opened:
                c.render_body()

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


def card_box() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(18, 14, 18, 14)
    return frame, lay
