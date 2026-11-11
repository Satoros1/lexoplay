"""Окно «Минута после игры»: реплики сессии с пропуском, слово выбирается одним нажатием (recap.py).

Появляется само, когда игра закрыта, и не забирает фокус: если человек уже занят другим, оно просто
лежит рядом. Esc закрывает в любой момент, отключается в настройках учёбы.
"""

from __future__ import annotations

import html
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lexoplay.novelwords import plural
from lexoplay.recap import BLANK, Question, Rare
from lexoplay.ui import theme
from lexoplay.ui.widgets import button, label

WRONG = "#A8452E"
WORDS = ("слово", "слова", "слов")


def _blanked_html(q: Question) -> str:
    a, b = q.blanked.split(BLANK, 1) if BLANK in q.blanked else (q.blanked, "")
    gap = f'<span style="color:{theme.MUTED}; letter-spacing:1px;">{BLANK}</span>'
    return f"{html.escape(a)}{gap}{html.escape(b)}"


def _filled_html(q: Question, color: str) -> str:
    word = html.escape(q.sentence[q.start : q.end])
    return (
        f"{html.escape(q.sentence[: q.start])}"
        f'<span style="color:{color}; font-weight:700;">{word}</span>'
        f"{html.escape(q.sentence[q.end :])}"
    )


class RecapDialog(QDialog):
    def __init__(
        self, ctl: Any, exe: str, title: str, questions: list[Question], rare: list[Rare], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.ctl, self.exe, self.game = ctl, exe, title
        self.questions, self.rare = questions, rare
        self.index = 0
        self.right = 0
        self.answered = False
        self.setWindowTitle("Минута после игры")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMinimumSize(600, 440)
        self.setStyleSheet(theme.stylesheet())
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)
        head = QHBoxLayout()
        head.addWidget(label("Минута после игры", "h2"))
        head.addStretch(1)
        self.progress = label("", "muted")
        head.addWidget(self.progress)
        lay.addLayout(head)
        self.sub = label(f"{title}: какое слово здесь стояло?", "muted", wrap=True)
        lay.addWidget(self.sub)

        self.sentence = label("", wrap=True)
        self.sentence.setTextFormat(Qt.TextFormat.RichText)
        self.sentence.setStyleSheet("font-size: 14pt; padding: 8px 0;")
        lay.addWidget(self.sentence)
        self.meaning = label("", "title", wrap=True)
        lay.addWidget(self.meaning)
        self.translation = label("", "muted", wrap=True)
        lay.addWidget(self.translation)

        self.options_box = QWidget()
        opts = QVBoxLayout(self.options_box)
        opts.setContentsMargins(0, 4, 0, 0)
        opts.setSpacing(8)
        self.options: list[QPushButton] = []
        for i in range(3):
            b = QPushButton("")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setMinimumHeight(40)
            b.clicked.connect(lambda _c=False, n=i: self._pick(n))
            opts.addWidget(b)
            self.options.append(b)
        lay.addWidget(self.options_box)

        self.rare_note = label("", "muted", wrap=True)
        lay.addWidget(self.rare_note)
        self.rare_list = QListWidget()
        self.rare_list.setObjectName("novelWords")
        lay.addWidget(self.rare_list, 1)
        lay.addStretch(1)

        bottom = QHBoxLayout()
        self.skip = QPushButton("Не сейчас")
        self.skip.setToolTip("Esc. Отключить совсем: настройки учёбы, «Минута после игры»")
        self.skip.clicked.connect(self.reject)
        bottom.addWidget(self.skip)
        bottom.addStretch(1)
        self.save = button(" В карточки", "primary", "check")
        self.save.clicked.connect(self._save)
        bottom.addWidget(self.save)
        self.next = button("Дальше", "primary")
        self.next.clicked.connect(self._next)
        bottom.addWidget(self.next)
        lay.addLayout(bottom)
        self._show_question()

    # ------------------------------------------------------------------ вопрос
    def _show_question(self) -> None:
        q = self.questions[self.index]
        self.answered = False
        self.progress.setText(f"{self.index + 1} из {len(self.questions)}")
        self.sentence.setText(_blanked_html(q))
        self.meaning.hide()
        self.translation.hide()
        self.rare_note.hide()
        self.rare_list.hide()
        self.save.hide()
        self.next.hide()
        self.options_box.show()
        for i, b in enumerate(self.options):
            b.setVisible(i < len(q.options))
            if i < len(q.options):
                b.setText(f"{i + 1}    {q.options[i]}")
                b.setEnabled(True)
                b.setStyleSheet("text-align: left; padding-left: 14px;")

    def _pick(self, n: int) -> None:
        q = self.questions[self.index]
        if self.answered or n >= len(q.options):
            return
        self.answered = True
        correct = q.options[n] == q.answer
        self.right += int(correct)
        for i, b in enumerate(self.options[: len(q.options)]):
            b.setEnabled(False)
            base = "text-align: left; padding-left: 14px;"
            if q.options[i] == q.answer:
                b.setStyleSheet(f"{base} background: {theme.GREEN_SOFT}; border-color: {theme.GREEN}; color: {theme.GREEN_INK};")
            elif i == n:
                b.setStyleSheet(f"{base} border-color: {WRONG}; color: {WRONG};")
        self.sentence.setText(_filled_html(q, theme.GREEN if correct else WRONG))
        self.meaning.setText(f"{q.answer} · {q.meaning}")
        self.meaning.show()
        self.translation.setText(q.translation)
        self.translation.setVisible(bool(q.translation))
        last = self.index + 1 >= len(self.questions)
        self.next.setText("Готово" if last and not self.rare else "Дальше")
        self.next.show()
        self.next.setFocus()
        fn = getattr(self.ctl, "recap_answer", None)
        if fn is not None:
            fn(self.exe, self.game, q, correct)

    def _next(self) -> None:
        if not self.answered:
            return
        if self.index + 1 < len(self.questions):
            self.index += 1
            self._show_question()
            return
        self._show_end()

    # ------------------------------------------------------------------ итог
    def _show_end(self) -> None:
        self.answered = False
        self.index = len(self.questions)
        self.progress.setText("")
        self.options_box.hide()
        self.meaning.hide()
        self.translation.hide()
        self.next.hide()
        missed = len(self.questions) - self.right
        self.sub.setText(self.game)
        self.sentence.setText(html.escape(f"{self.right} из {len(self.questions)}"))
        if not self.rare:
            self.rare_note.setText(
                "Слова, где ошибся, в игре ещё пару раз покажутся со значением." if missed else "Все слова на месте."
            )
            self.rare_note.show()
            self.skip.setText("Закрыть")
            return
        tail = " Слова, где ошибся, в игре ещё пару раз покажутся со значением." if missed else ""
        self.rare_note.setText(
            "Эти слова в игре почти не повторятся, так что сама она им не научит, а в английском они нередкие. "
            "Отправить в карточки с репликой из игры?" + tail
        )
        self.rare_note.show()
        self.rare_list.clear()
        for r in self.rare:
            item = QListWidgetItem(f"{r.key}    ·    {r.meaning}")
            item.setData(Qt.ItemDataRole.UserRole, r.key)
            item.setToolTip(r.sentence)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.rare_list.addItem(item)
        self.rare_list.itemChanged.connect(lambda _i: self._update_save())
        self.rare_list.show()
        self.save.show()
        self.skip.setText("Закрыть")
        self._update_save()

    def _checked(self) -> list[Rare]:
        keys = {
            str(self.rare_list.item(i).data(Qt.ItemDataRole.UserRole))
            for i in range(self.rare_list.count())
            if self.rare_list.item(i).checkState() == Qt.CheckState.Checked
        }
        return [r for r in self.rare if r.key in keys]

    def _update_save(self) -> None:
        n = len(self._checked())
        self.save.setEnabled(bool(n))
        self.save.setText(f" В карточки: {plural(n, WORDS)}" if n else " В карточки")

    def _save(self) -> None:
        picked = self._checked()
        fn = getattr(self.ctl, "recap_save", None)
        if fn is not None and picked:
            fn(self.exe, self.game, picked)
        self.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        if not self.answered and self.index < len(self.questions) and Qt.Key.Key_1 <= key <= Qt.Key.Key_3:
            self._pick(key - Qt.Key.Key_1)
            return
        if self.answered and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self._next()
            return
        super().keyPressEvent(event)
