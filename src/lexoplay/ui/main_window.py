"""Главное окно LexoPlay: Игры, Lexo, Клавиши, Вид, Ещё.

Настройки применяются сразу, без кнопки «Сохранить». Закрытие окна прячет его в трей:
LexoPlay продолжает работать, пока его не закроют через меню трея.
Вся логика в контроллере (App); окно только показывает и передаёт действия.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QCloseEvent, QColor, QDesktopServices, QFont, QFontMetricsF, QPainter
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lexoplay import __version__
from lexoplay.config import HOTKEY_LABELS, Hotkeys, Settings
from lexoplay.games import WindowInfo
from lexoplay.geometry import font_px_for
from lexoplay.hotkeys import HotkeyError, parse, risk
from lexoplay.lexo import Deck
from lexoplay.store import Game, Store
from lexoplay.ui import theme
from lexoplay.ui.hotkey_edit import HotkeyEdit
from lexoplay.ui.theme import app_icon, draw_logo, icon

RELEASES_URL = "https://github.com/Satoros1/lexoplay/releases/latest"


@dataclass(frozen=True, slots=True)
class AccountStatus:
    linked: bool
    label: str = ""  # @username
    premium: bool = False
    used: int | None = None
    limit: int | None = None
    offline: bool = False
    queue: int = 0
    failed: int = 0
    note: str = ""  # «На сервере Lexo ещё нет LexoPlay…»


@dataclass
class DeckState:
    decks: list[Deck] = field(default_factory=list)
    current_id: int | None = None
    loaded: bool = False
    error: str | None = None


class Controller(Protocol):
    settings: Settings
    store: Store

    def account_status(self) -> AccountStatus: ...
    def link(self, code: str, done: Callable[[str | None], None]) -> None: ...
    def unlink(self, done: Callable[[], None]) -> None: ...
    def load_decks(self, done: Callable[[list[Deck], int | None, str | None], None]) -> None: ...
    def create_deck(self, title: str, done: Callable[[Deck | None, str | None], None]) -> None: ...
    def list_windows(self) -> list[WindowInfo]: ...
    def add_game(self, w: WindowInfo) -> Game: ...
    def pick_region(self, exe: str) -> None: ...
    def forget_game(self, exe: str) -> None: ...
    def save_game(self, game: Game) -> None: ...
    def active_exe(self) -> str | None: ...
    def settings_changed(self) -> None: ...
    def pause_hotkeys(self, paused: bool) -> None: ...
    def api_key(self) -> str: ...
    def set_api_key(self, key: str) -> None: ...
    def retry_failed(self) -> None: ...
    def make_report(self) -> str | None: ...
    def open_logs(self) -> None: ...
    def window_hidden(self) -> None: ...


# ------------------------------------------------------------------ мелкие детали
def label(text: str, name: str | None = None, *, wrap: bool = False) -> QLabel:
    lbl = QLabel(text)
    if name:
        lbl.setObjectName(name)
    lbl.setWordWrap(wrap)
    return lbl


def card(name: str = "card") -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName(name)
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(18, 16, 18, 16)
    lay.setSpacing(10)
    return frame, lay


def button(text: str, kind: str | None = None, icon_name: str | None = None) -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    if icon_name:
        b.setIcon(icon(icon_name, "#FFFFFF" if kind == "primary" else theme.INK, 16))
        b.setIconSize(QSize(16, 16))
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def keycap(text: str) -> QLabel:
    return label(text or "нет", "keycap")


def clear(layout) -> None:  # type: ignore[no-untyped-def]
    """Убрать всё из раскладки сразу: deleteLater сработает позже, а виджет до этого висел бы поверх."""
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.hide()
            w.setParent(None)
            w.deleteLater()
        elif item.layout() is not None:
            clear(item.layout())


def tile_color(seed: str) -> QColor:
    palette = ["#59506F", "#3D5A80", "#8A5A44", "#2F6F62", "#7A4E7E", "#4B6584", "#806A2F"]
    h = int(hashlib.md5(seed.encode()).hexdigest(), 16)
    return QColor(palette[h % len(palette)])


class Tile(QWidget):
    """Квадратик игры с первой буквой названия."""

    def __init__(self, title: str, seed: str, size: int = 44) -> None:
        super().__init__()
        self._title, self._seed = title, seed
        self.setFixedSize(size, size)

    def paintEvent(self, _e) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(tile_color(self._seed))
        p.drawRoundedRect(QRectF(self.rect()), 10, 10)
        f = theme.ui_font(14, QFont.Weight.Bold)
        p.setFont(f)
        p.setPen(QColor("#FFFFFF"))
        letter = next((c for c in self._title if c.isalnum()), "?").upper()
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, letter)
        p.end()


def page_shell(title: str, subtitle: str) -> tuple[QScrollArea, QVBoxLayout, QHBoxLayout]:
    """Страница с прокруткой: заголовок, подзаголовок, место под кнопки справа и тело."""
    inner = QWidget()
    inner.setObjectName("page")
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(32, 28, 32, 28)
    lay.setSpacing(16)
    head = QHBoxLayout()
    col = QVBoxLayout()
    col.setSpacing(2)
    col.addWidget(label(title, "h1"))
    col.addWidget(label(subtitle, "muted", wrap=True))
    head.addLayout(col, 1)
    actions = QHBoxLayout()
    actions.setSpacing(8)
    head.addLayout(actions)
    lay.addLayout(head)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidget(inner)
    return scroll, lay, actions


# ------------------------------------------------------------------ Игры
class GamesPage(QWidget):
    go_lexo = Signal()

    def __init__(self, ctl: Controller, decks: DeckState) -> None:
        super().__init__()
        self.ctl, self.decks = ctl, decks
        scroll, lay, actions = page_shell("Игры", "Перевод и слова работают в играх из этого списка.")
        add = button(" Добавить игру", "primary", "plus")
        add.clicked.connect(self.add_game)
        actions.addWidget(add)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self.banner, bl = card("banner")
        row = QHBoxLayout()
        self.banner_text = label("", wrap=True)
        row.addWidget(self.banner_text, 1)
        fix = button("Настроить")
        fix.clicked.connect(self.go_lexo.emit)
        row.addWidget(fix)
        bl.addLayout(row)
        lay.addWidget(self.banner)

        self.list = QVBoxLayout()
        self.list.setSpacing(10)
        lay.addLayout(self.list)

        lay.addSpacing(6)
        lay.addWidget(label("КАК ПОЛЬЗОВАТЬСЯ", "section"))
        self.howto, self.howto_lay = card()
        lay.addWidget(self.howto)
        lay.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        st = self.ctl.account_status()
        has_key = bool(self.ctl.api_key())
        if not st.linked and not has_key:
            self.banner_text.setText(
                "Перевод пока не настроен. Привяжи ПК к Lexo (тогда и слова будут сохраняться) "
                "или вставь свой ключ Anthropic во вкладке «Ещё»."
            )
            self.banner.show()
        elif st.note:
            self.banner_text.setText(st.note)
            self.banner.show()
        else:
            self.banner.hide()

        clear(self.list)
        games = self.ctl.store.list_games()
        active = self.ctl.active_exe()
        if not games:
            frame, fl = card()
            fl.addWidget(label("Игр пока нет", "title"))
            fl.addWidget(
                label(
                    "Запусти игру, нажми «Добавить игру» и выбери её окно. Потом в игре нажми "
                    f"{self.ctl.settings.hotkeys.translate} и выдели мышкой окно диалога.",
                    "muted",
                    wrap=True,
                )
            )
            self.list.addWidget(frame)
        for g in games:
            self.list.addWidget(self._game_card(g, g.exe == active, st.linked))

        clear(self.howto_lay)
        hk = self.ctl.settings.hotkeys
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        rows = [
            (hk.translate, "перевести реплику, ещё раз спрятать"),
            (hk.words, "подчеркнуть слова: клик по слову, «+ в Lexo» или Enter. Shift+клик выбирает фразу"),
            (hk.hide, "спрятать перевод"),
            (hk.reselect, "выделить окно диалога заново, если перевод встал не туда"),
        ]
        for i, (key, text) in enumerate(rows):
            grid.addWidget(keycap(key), i, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(label(text, wrap=True), i, 1)
        grid.setColumnStretch(1, 1)
        self.howto_lay.addLayout(grid)
        self.howto_lay.addWidget(
            label(
                "Игра должна быть в окне или в окне без рамки. Маленькие игры (800×600) растягивай через Magpie.",
                "muted",
                wrap=True,
            )
        )

    def _game_card(self, g: Game, active: bool, linked: bool) -> QFrame:
        frame = QFrame()
        frame.setObjectName("cardActive" if active else "card")
        row = QHBoxLayout(frame)
        row.setContentsMargins(16, 14, 16, 14)
        row.setSpacing(14)
        row.addWidget(Tile(g.title, g.exe))
        col = QVBoxLayout()
        col.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(8)
        name = label(g.title, "title")
        name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        top.addWidget(name)
        if active:
            top.addWidget(label("сейчас в игре", "chip"))
        top.addStretch(1)
        col.addLayout(top)
        if g.region is not None:
            state = label(f"{g.exe} · окно диалога выделено", "muted")
        else:
            state = label(f"{g.exe} · окно диалога ещё не выделено: открой игру и нажми {self.ctl.settings.hotkeys.translate}", "warn")
        state.setWordWrap(True)
        col.addWidget(state)
        if linked:
            deck_row = QHBoxLayout()
            deck_row.setSpacing(8)
            deck_row.addWidget(label("Слова в колоду", "muted"))
            deck_row.addWidget(self._deck_combo(g), 0)
            deck_row.addStretch(1)
            col.addLayout(deck_row)
        row.addLayout(col, 1)

        pick = button(" Выделить окно диалога", None, "scan")
        pick.setToolTip("Откроет игру и даст заново обвести окно диалога")
        pick.clicked.connect(lambda: self.ctl.pick_region(g.exe))
        row.addWidget(pick, 0, Qt.AlignmentFlag.AlignTop)
        more = QToolButton()
        more.setIcon(icon("dots", size=18))
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(more)
        rename = QAction("Переименовать…", menu)
        rename.triggered.connect(lambda: self._rename(g))
        forget = QAction("Убрать из списка", menu)
        forget.triggered.connect(lambda: self._forget(g))
        menu.addAction(rename)
        menu.addSeparator()
        menu.addAction(forget)
        more.setMenu(menu)
        row.addWidget(more, 0, Qt.AlignmentFlag.AlignTop)
        return frame

    def _deck_combo(self, g: Game) -> QComboBox:
        combo = QComboBox()
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(min(44, max(24, len(g.title) + 16)))
        combo.addItem(f"Своя колода «{g.title}»", ("game", None, None))
        combo.addItem("Текущая колода в Lexo", ("current", None, None))
        if self.decks.decks:
            combo.insertSeparator(combo.count())
            for d in self.decks.decks:
                if d.title == g.title:
                    continue  # это и есть «своя колода»
                combo.addItem(f"«{d.title}» · {d.card_count}", ("deck", d.id, d.title))
        combo.insertSeparator(combo.count())
        combo.addItem("Новая колода…", ("new", None, None))
        current = 0
        for i in range(combo.count()):
            data = combo.itemData(i)
            if not data:
                continue
            if g.deck_mode == "deck" and data[0] == "deck" and data[1] == g.deck_id:
                current = i
            elif g.deck_mode == "current" and data[0] == "current":
                current = i
        if g.deck_mode == "deck" and current == 0 and g.deck_title:
            combo.insertItem(2, f"«{g.deck_title}»", ("deck", g.deck_id, g.deck_title))
            current = 2
        combo.setCurrentIndex(current)
        combo.activated.connect(lambda i, c=combo, game=g: self._deck_chosen(game, c, i))
        return combo

    def _deck_chosen(self, g: Game, combo: QComboBox, index: int) -> None:
        mode, deck_id, title = combo.itemData(index)
        fresh = self.ctl.store.get_game(g.exe) or g
        if mode == "new":
            text, ok = QInputDialog.getText(self, "Новая колода", "Название колоды в Lexo:", text=fresh.title)
            if not ok or not text.strip():
                self.refresh()
                return

            def made(deck: Deck | None, err: str | None) -> None:
                if deck is None:
                    self.window().show_message(err or "Не получилось создать колоду.")  # type: ignore[attr-defined]
                    self.refresh()
                    return
                self.decks.decks.insert(0, deck)
                self.ctl.save_game(fresh.with_(deck_mode="deck", deck_id=deck.id, deck_title=deck.title))
                self.refresh()

            self.ctl.create_deck(text.strip(), made)
            return
        self.ctl.save_game(fresh.with_(deck_mode=mode, deck_id=deck_id, deck_title=title))

    def _rename(self, g: Game) -> None:
        text, ok = QInputDialog.getText(self, "Название игры", "Так игра будет называться в Lexo и в итогах:", text=g.title)
        if ok and text.strip():
            self.ctl.save_game(g.with_(title=text.strip()[:128]))
            self.refresh()

    def _forget(self, g: Game) -> None:
        self.ctl.forget_game(g.exe)
        self.refresh()

    def add_game(self) -> None:
        from lexoplay.ui.window_picker import WindowPicker

        known = {g.exe for g in self.ctl.store.list_games()}
        dlg = WindowPicker(self.ctl.list_windows, known, self.window())
        if dlg.exec() and dlg.chosen is not None:
            self.ctl.add_game(dlg.chosen)
            self.refresh()


# ------------------------------------------------------------------ Lexo
class LexoPage(QWidget):
    changed = Signal()

    def __init__(self, ctl: Controller, decks: DeckState) -> None:
        super().__init__()
        self.ctl, self.decks = ctl, decks
        scroll, lay, actions = page_shell("Lexo", "Аккаунт, переводы на сегодня и колоды для слов из игр.")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        lay.addWidget(label("АККАУНТ", "section"))
        self.account, self.account_lay = card()
        lay.addWidget(self.account)

        head = QHBoxLayout()
        head.addWidget(label("КОЛОДЫ", "section"))
        head.addStretch(1)
        self.reload_btn = button(" Обновить", None, "refresh")
        self.reload_btn.clicked.connect(self.reload_decks)
        self.new_deck_btn = button(" Новая колода", None, "plus")
        self.new_deck_btn.clicked.connect(self.new_deck)
        head.addWidget(self.reload_btn)
        head.addWidget(self.new_deck_btn)
        lay.addLayout(head)
        self.table, self.table_lay = card()
        self.table_lay.setSpacing(0)
        self.table_lay.setContentsMargins(0, 6, 0, 6)
        lay.addWidget(self.table)

        lay.addWidget(label("СЛОВА ИЗ НОВЫХ ИГР", "section"))
        dflt, dl = card()
        row = QHBoxLayout()
        row.addWidget(label("Куда класть слова игры, для которой колода не выбрана"), 1)
        self.default_deck = QComboBox()
        self.default_deck.addItem("В свою колоду с названием игры", "game")
        self.default_deck.addItem("В текущую колоду Lexo", "current")
        self.default_deck.setCurrentIndex(max(0, self.default_deck.findData(ctl.settings.deck)))
        self.default_deck.activated.connect(self._default_deck)
        row.addWidget(self.default_deck)
        dl.addLayout(row)
        dl.addWidget(label("Для каждой игры колоду можно поменять во вкладке «Игры».", "muted"))
        lay.addWidget(dflt)
        lay.addStretch(1)
        self.refresh()

    def _default_deck(self) -> None:
        self.ctl.settings.deck = self.default_deck.currentData()
        self.ctl.settings_changed()

    # --- аккаунт
    def refresh(self) -> None:
        clear(self.account_lay)
        st = self.ctl.account_status()
        if st.linked:
            self._linked(st)
        else:
            self._link_form(st)
        self._render_decks(st.linked)

    def _linked(self, st: AccountStatus) -> None:
        top = QHBoxLayout()
        top.setSpacing(12)
        top.addWidget(Tile(st.label.lstrip("@") or "L", st.label, 40))
        col = QVBoxLayout()
        col.setSpacing(2)
        line = QHBoxLayout()
        line.addWidget(label(st.label or "Аккаунт Lexo", "title"))
        line.addWidget(label("Premium" if st.premium else "Бесплатно", "chip" if st.premium else "chipGray"))
        line.addStretch(1)
        col.addLayout(line)
        col.addWidget(label("ПК привязан: перевод идёт через Lexo, слова сохраняются в карточки.", "muted", wrap=True))
        top.addLayout(col, 1)
        unlink = button("Отвязать этот ПК", "danger")
        unlink.clicked.connect(self._unlink)
        top.addWidget(unlink, 0, Qt.AlignmentFlag.AlignTop)
        self.account_lay.addLayout(top)

        if st.offline:
            self.account_lay.addWidget(label("Нет связи с сервером Lexo. Перевод и слова заработают, как только связь вернётся.", "warn", wrap=True))
        if st.used is not None and st.limit:
            bar_row = QHBoxLayout()
            bar_row.addWidget(label("Переводов сегодня", "muted"))
            bar_row.addStretch(1)
            bar_row.addWidget(label(f"{st.used} из {st.limit}", "ok"))
            self.account_lay.addLayout(bar_row)
            bar = QProgressBar()
            bar.setTextVisible(False)
            bar.setRange(0, st.limit)
            bar.setValue(min(st.used, st.limit))
            self.account_lay.addWidget(bar)
        if st.queue or st.failed:
            q = QHBoxLayout()
            parts = []
            if st.queue:
                parts.append(f"ждут отправки: {st.queue}")
            if st.failed:
                parts.append(f"не отправились: {st.failed}")
            q.addWidget(label("Слова и итоги " + ", ".join(parts), "warn"), 1)
            if st.failed:
                again = button("Отправить ещё раз")
                again.clicked.connect(lambda: (self.ctl.retry_failed(), QTimer.singleShot(800, self.refresh)))
                q.addWidget(again)
            self.account_lay.addLayout(q)
        if st.note:
            self.account_lay.addWidget(label(st.note, "warn", wrap=True))

    def _link_form(self, st: AccountStatus) -> None:
        self.account_lay.addWidget(label("Привяжи ПК к Lexo", "title"))
        self.account_lay.addWidget(
            label(
                "Тогда перевод пойдёт через твой аккаунт, без своего ключа, а слова из игр станут карточками "
                "в твоих колодах. Код возьми в боте командой /link или в Mini App: Профиль → Подключить ПК.",
                "muted",
                wrap=True,
            )
        )
        row = QHBoxLayout()
        self.code = QLineEdit()
        self.code.setObjectName("code")
        self.code.setPlaceholderText("K7QX2M")
        self.code.setMaxLength(8)
        self.code.setMaximumWidth(220)
        self.code.textEdited.connect(lambda t: self.code.setText(t.upper().replace(" ", "")))
        self.code.returnPressed.connect(self._link)
        row.addWidget(self.code)
        self.link_btn = button(" Привязать", "primary", "link")
        self.link_btn.clicked.connect(self._link)
        row.addWidget(self.link_btn)
        row.addStretch(1)
        self.account_lay.addLayout(row)
        self.link_status = label(st.note, "warn" if st.note else "muted", wrap=True)
        self.link_status.setVisible(bool(st.note))
        self.account_lay.addWidget(self.link_status)

    def _link(self) -> None:
        code = self.code.text().strip()
        if len(code) < 4:
            self._status("Введи код из бота: шесть букв и цифр.", error=True)
            return
        self.link_btn.setEnabled(False)
        self._status("Привязываю…")

        def done(err: str | None) -> None:
            if err:
                self.link_btn.setEnabled(True)
                self._status(err, error=True)
                return
            self.refresh()
            self.reload_decks()
            self.changed.emit()

        self.ctl.link(code, done)

    def _status(self, text: str, *, error: bool = False) -> None:
        self.link_status.setText(text)
        self.link_status.setObjectName("error" if error else "muted")
        self.link_status.style().unpolish(self.link_status)
        self.link_status.style().polish(self.link_status)
        self.link_status.show()

    def _unlink(self) -> None:
        def done() -> None:
            self.decks.decks, self.decks.loaded = [], False
            self.refresh()
            self.changed.emit()

        self.ctl.unlink(done)

    # --- колоды
    def reload_decks(self) -> None:
        if not self.ctl.account_status().linked:
            return
        self.reload_btn.setEnabled(False)

        def done(decks: list[Deck], current: int | None, err: str | None) -> None:
            self.reload_btn.setEnabled(True)
            if err is None:
                self.decks.decks, self.decks.current_id, self.decks.loaded = decks, current, True
            self.decks.error = err
            self._render_decks(True)
            self.changed.emit()

        self.ctl.load_decks(done)

    def new_deck(self) -> None:
        text, ok = QInputDialog.getText(self, "Новая колода", "Название колоды в Lexo:")
        if not ok or not text.strip():
            return

        def done(deck: Deck | None, err: str | None) -> None:
            if deck is None:
                self.decks.error = err
            self.reload_decks()

        self.ctl.create_deck(text.strip(), done)

    def _render_decks(self, linked: bool) -> None:
        clear(self.table_lay)
        self.reload_btn.setEnabled(linked)
        self.new_deck_btn.setEnabled(linked)
        if not linked:
            self.table_lay.addWidget(self._pad(label("Колоды появятся здесь после привязки к Lexo.", "muted")))
            return
        if self.decks.error:
            self.table_lay.addWidget(self._pad(label(self.decks.error, "warn", wrap=True)))
        if not self.decks.loaded:
            self.table_lay.addWidget(self._pad(label("Загружаю колоды…", "muted")))
            return
        if not self.decks.decks:
            self.table_lay.addWidget(self._pad(label("Колод пока нет: первая появится вместе с первым словом из игры.", "muted", wrap=True)))
            return
        self.table_lay.addWidget(self._deck_row("Колода", "Новые", "Повторить", "Всего", header=True))
        for i, d in enumerate(self.decks.decks):
            current = " · текущая" if d.id == self.decks.current_id else ""
            self.table_lay.addWidget(
                self._deck_row(d.title + current, str(d.new_count), str(d.due_count), str(d.card_count), last=i == len(self.decks.decks) - 1)
            )

    def _pad(self, w: QWidget) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(18, 12, 18, 12)
        lay.addWidget(w)
        return box

    def _deck_row(self, title: str, new: str, due: str, total: str, *, header: bool = False, last: bool = False) -> QFrame:
        row = QFrame()
        row.setObjectName("rowLast" if last else "row")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(18, 9, 18, 9)
        name = "section" if header else None
        t = label(title.upper() if header else title, name)
        t.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lay.addWidget(t, 1)
        for text, color in ((new, "#2B6CB0"), (due, theme.GREEN), (total, theme.MUTED)):
            cell = label(text.upper() if header else text, name)
            cell.setFixedWidth(84)
            cell.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if not header:
                cell.setStyleSheet(f"color: {color if text != '0' else '#B0ADA4'}; font-weight: 700;")
            lay.addWidget(cell)
        return row


# ------------------------------------------------------------------ Клавиши
class KeysPage(QWidget):
    def __init__(self, ctl: Controller) -> None:
        super().__init__()
        self.ctl = ctl
        scroll, lay, actions = page_shell(
            "Клавиши",
            "Работают, только пока на экране игра. В браузере и других программах эти клавиши ведут себя как обычно.",
        )
        reset = button("Вернуть как было")
        reset.clicked.connect(self._reset)
        actions.addWidget(reset)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self.box, self.box_lay = card()
        self.box_lay.setSpacing(0)
        self.box_lay.setContentsMargins(0, 4, 0, 4)
        lay.addWidget(self.box)
        lay.addWidget(
            label(
                "Нажми на клавишу справа, потом новую клавишу или сочетание из двух, например Alt+Q. "
                "Esc отменяет, Backspace очищает «Добавить текущее окно». Ctrl и Shift в новеллах обычно "
                "пропускают текст, поэтому лучше свободные F-клавиши: F2, F3, F4, F6, F7…",
                "muted",
                wrap=True,
            )
        )
        lay.addStretch(1)
        self.edits: dict[str, HotkeyEdit] = {}
        self.notes: dict[str, QLabel] = {}
        self._build()

    def _build(self) -> None:
        clear(self.box_lay)
        self.edits.clear()
        self.notes.clear()
        items = self.ctl.settings.hotkeys.items()
        for i, (name, value) in enumerate(items):
            row = QFrame()
            row.setObjectName("rowLast" if i == len(items) - 1 else "row")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(18, 12, 18, 12)
            col = QVBoxLayout()
            col.setSpacing(2)
            title, desc = HOTKEY_LABELS[name]
            col.addWidget(label(title, "title"))
            if desc:
                col.addWidget(label(desc, "muted", wrap=True))
            note = label("", "warn", wrap=True)
            note.hide()
            col.addWidget(note)
            rl.addLayout(col, 1)
            edit = HotkeyEdit(value, allow_empty=name == "add_game")
            edit.clicked.connect(lambda: self.ctl.pause_hotkeys(True))
            edit.changed.connect(lambda text, n=name: self._changed(n, text))
            rl.addWidget(edit, 0, Qt.AlignmentFlag.AlignVCenter)
            self.box_lay.addWidget(row)
            self.edits[name] = edit
            self.notes[name] = note
        self._validate()

    def _changed(self, name: str, text: str) -> None:
        setattr(self.ctl.settings.hotkeys, name, text)
        self._validate()
        self.ctl.pause_hotkeys(False)
        self.ctl.settings_changed()

    def _validate(self) -> None:
        values = [(n, v) for n, v in self.ctl.settings.hotkeys.items()]
        owners: dict[str, list[str]] = {}
        for name, value in values:
            if value:
                owners.setdefault(value.lower(), []).append(name)
        for name, value in values:
            note = self.notes[name]
            msg = None
            if value:
                try:
                    parse(value)
                    msg = risk(value)
                except HotkeyError as exc:
                    msg = f"Эта клавиша не подойдёт: {exc}"
                others = [o for o in owners.get(value.lower(), []) if o != name]
                if others:
                    msg = f"Эта же клавиша стоит на «{HOTKEY_LABELS[others[0]][0]}»: оставь её только в одном месте."
            note.setText(msg or "")
            note.setVisible(bool(msg))

    def _reset(self) -> None:
        self.ctl.settings.hotkeys = Hotkeys()
        self.ctl.settings_changed()
        self._build()


# ------------------------------------------------------------------ Вид
class StripPreview(QWidget):
    """Как строка перевода выглядит поверх игры при текущих размере и плотности."""

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.setMinimumHeight(190)

    def paintEvent(self, _e) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#1E2433"))
        p.drawRoundedRect(r, 12, 12)
        p.setBrush(QColor("#2A3146"))
        p.drawRoundedRect(QRectF(r.x(), r.bottom() - r.height() * 0.45, r.width(), r.height() * 0.45), 12, 12)
        p.setBrush(QColor("#59506F"))
        p.drawRoundedRect(QRectF(r.width() * 0.44, r.height() * 0.08, r.width() * 0.12, r.height() * 0.62), 40, 40)
        # окно диалога
        dlg = QRectF(r.x() + 18, r.bottom() - 58, r.width() - 36, 44)
        p.setBrush(QColor(250, 248, 242, 240))
        p.drawRoundedRect(dlg, 8, 8)
        f = QFont("Segoe UI")
        f.setPixelSize(13)
        p.setFont(f)
        p.setPen(QColor("#2C2C2A"))
        p.drawText(dlg.adjusted(14, 0, -14, 0), int(Qt.AlignmentFlag.AlignVCenter), "I was reluctant to come here tonight, you know.")
        # строка перевода над ним
        px = font_px_for(720, 19.0, self.settings.font_scale)
        sf = QFont("Segoe UI")
        sf.setPixelSize(round(px))
        sf.setWeight(QFont.Weight.Medium)
        fm = QFontMetricsF(sf)
        text = "Знаешь, мне совсем не хотелось сюда сегодня приходить."
        th = fm.boundingRect(QRectF(0, 0, dlg.width() - 2 * px * 0.9, 1000), int(Qt.TextFlag.TextWordWrap), text).height()
        strip = QRectF(dlg.x(), dlg.y() - 8 - (th + px * 1.2), dlg.width(), th + px * 1.2)
        bg = QColor(theme.STRIP_BG)
        bg.setAlphaF(max(0.3, min(1.0, self.settings.strip_opacity)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(strip, min(10.0, strip.height() / 2), min(10.0, strip.height() / 2))
        p.setFont(sf)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(strip.adjusted(px * 0.9, px * 0.6, -px * 0.9, 0), int(Qt.TextFlag.TextWordWrap), text)
        p.end()


class ViewPage(QWidget):
    def __init__(self, ctl: Controller) -> None:
        super().__init__()
        self.ctl = ctl
        s = ctl.settings
        scroll, lay, _actions = page_shell("Вид", "Как перевод выглядит поверх игры.")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        lay.addWidget(label("СТРОКА ПЕРЕВОДА", "section"))
        box, bl = card()
        self.preview = StripPreview(s)
        bl.addWidget(self.preview)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        self.size = QSlider(Qt.Orientation.Horizontal)
        self.size.setRange(70, 180)
        self.size.setValue(round(s.font_scale * 100))
        self.size_val = label("", "muted")
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(40, 100)
        self.opacity.setValue(round(s.strip_opacity * 100))
        self.opacity_val = label("", "muted")
        grid.addWidget(label("Размер текста"), 0, 0)
        grid.addWidget(self.size, 0, 1)
        grid.addWidget(self.size_val, 0, 2)
        grid.addWidget(label("Плотность фона"), 1, 0)
        grid.addWidget(self.opacity, 1, 1)
        grid.addWidget(self.opacity_val, 1, 2)
        grid.setColumnStretch(1, 1)
        bl.addLayout(grid)
        lay.addWidget(box)
        self.size.valueChanged.connect(self._sliders)
        self.opacity.valueChanged.connect(self._sliders)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(250)
        self._save_timer.timeout.connect(self.ctl.settings_changed)
        self._labels()

        lay.addWidget(label("РАСТЯНУТА ЛИ ИГРА", "section"))
        modes = QHBoxLayout()
        modes.setSpacing(10)
        self.group = QButtonGroup(self)
        for code, title, desc in (
            ("auto", "Определять самому", "LexoPlay сам видит, что игра растянута через Magpie"),
            ("always", "Всегда на весь экран", "Игра всегда растянута Magpie или своим полноэкранным режимом"),
            ("never", "Обычное окно", "Перевод ставится прямо по окну игры"),
        ):
            frame = QFrame()
            frame.setObjectName("cardActive" if s.magpie_mode == code else "card")
            fl = QVBoxLayout(frame)
            fl.setContentsMargins(14, 12, 14, 12)
            rb = QRadioButton(title)
            rb.setChecked(s.magpie_mode == code)
            self.group.addButton(rb)
            rb.toggled.connect(lambda on, c=code: on and self._mode(c))
            fl.addWidget(rb)
            fl.addWidget(label(desc, "muted", wrap=True))
            modes.addWidget(frame, 1)
            frame.setProperty("mode", code)
        lay.addLayout(modes)
        self.mode_frames = [modes.itemAt(i).widget() for i in range(modes.count())]
        lay.addWidget(
            label(
                "Старая игра не показывает перевод поверх себя? Переведи её в оконный режим "
                "(в настройках игры, через dgVoodoo2 или DxWnd) и растяни Magpie.",
                "warn",
                wrap=True,
            )
        )
        lay.addStretch(1)

    def _labels(self) -> None:
        self.size_val.setText(f"{self.size.value()}%")
        self.opacity_val.setText(f"{self.opacity.value()}%")

    def _sliders(self) -> None:
        self.ctl.settings.font_scale = self.size.value() / 100
        self.ctl.settings.strip_opacity = self.opacity.value() / 100
        self._labels()
        self.preview.update()
        self._save_timer.start()

    def _mode(self, code: str) -> None:
        self.ctl.settings.magpie_mode = code
        for f in self.mode_frames:
            f.setObjectName("cardActive" if f.property("mode") == code else "card")
            f.style().unpolish(f)
            f.style().polish(f)
        self.ctl.settings_changed()


# ------------------------------------------------------------------ Ещё
class MorePage(QWidget):
    def __init__(self, ctl: Controller) -> None:
        super().__init__()
        self.ctl = ctl
        s = ctl.settings
        scroll, lay, _actions = page_shell("Ещё", "Свой ключ, сервер и помощь, если что-то не работает.")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        lay.addWidget(label("БЕЗ LEXO", "section"))
        box, bl = card()
        bl.addWidget(
            label(
                "Можно переводить своим ключом Anthropic, не привязывая ПК. Слова тогда в карточки не сохраняются. "
                "Если ПК привязан к Lexo, ключ не используется.",
                "muted",
                wrap=True,
            )
        )
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        self.key = QLineEdit(ctl.api_key())
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("sk-ant-…")
        self.key.editingFinished.connect(lambda: self.ctl.set_api_key(self.key.text().strip()))
        show = QPushButton("Показать")
        show.setCheckable(True)
        show.toggled.connect(lambda on: self.key.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        self.model = QLineEdit(s.model)
        self.model.editingFinished.connect(self._model)
        form.addWidget(label("Ключ Anthropic"), 0, 0)
        form.addWidget(self.key, 0, 1)
        form.addWidget(show, 0, 2)
        form.addWidget(label("Модель"), 1, 0)
        form.addWidget(self.model, 1, 1, 1, 2)
        bl.addLayout(form)
        lay.addWidget(box)

        lay.addWidget(label("СЕРВЕР", "section"))
        box2, b2 = card()
        row = QHBoxLayout()
        row.addWidget(label("Адрес сервера Lexo"))
        self.server = QLineEdit(s.server_url)
        self.server.editingFinished.connect(self._server)
        row.addWidget(self.server, 1)
        b2.addLayout(row)
        lay.addWidget(box2)

        lay.addWidget(label("ПОМОЩЬ", "section"))
        box3, b3 = card()
        self.debug = QCheckBox("Сохранять кадры и распознанный текст (для поиска ошибок)")
        self.debug.setChecked(s.debug)
        self.debug.toggled.connect(self._debug)
        b3.addWidget(self.debug)
        btns = QHBoxLayout()
        report = button(" Собрать отчёт об ошибке", None, "send")
        report.clicked.connect(self._report)
        logs = button(" Папка с логами", None, "folder")
        logs.clicked.connect(self.ctl.open_logs)
        btns.addWidget(report)
        btns.addWidget(logs)
        btns.addStretch(1)
        b3.addLayout(btns)
        self.report_note = label("", "muted", wrap=True)
        self.report_note.hide()
        b3.addWidget(self.report_note)
        lay.addWidget(box3)

        ver = QHBoxLayout()
        ver.addWidget(label(f"LexoPlay {__version__}", "version"))
        upd = button("Скачать новую версию", "link")
        upd.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(RELEASES_URL)))
        ver.addWidget(upd)
        ver.addStretch(1)
        lay.addLayout(ver)
        lay.addStretch(1)

    def _model(self) -> None:
        self.ctl.settings.model = self.model.text().strip() or self.ctl.settings.model
        self.ctl.settings_changed()

    def _server(self) -> None:
        self.ctl.settings.server_url = self.server.text().strip().rstrip("/") or self.ctl.settings.server_url
        self.ctl.settings_changed()

    def _debug(self, on: bool) -> None:
        self.ctl.settings.debug = on
        self.ctl.settings_changed()

    def _report(self) -> None:
        path = self.ctl.make_report()
        self.report_note.setText(
            f"Готово: {path}. Пришли этот файл в чат, там лог и настройки без ключей и токенов."
            if path
            else "Не получилось собрать отчёт, подробности в логе."
        )
        self.report_note.show()


# ------------------------------------------------------------------ окно
class MainWindow(QWidget):
    PAGES = (("games", "Игры", "games"), ("lexo", "Lexo", "lexo"), ("keys", "Клавиши", "keys"), ("view", "Вид", "view"), ("more", "Ещё", "more"))

    def __init__(self, ctl: Controller) -> None:
        super().__init__()
        self.ctl = ctl
        self.setObjectName("root")
        self.setWindowTitle("LexoPlay")
        self.setWindowIcon(app_icon())
        self.resize(980, 680)
        self.setMinimumSize(820, 560)
        self.setStyleSheet(theme.stylesheet())
        self.decks = DeckState()

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        side = QWidget()
        side.setObjectName("sidebar")
        side.setFixedWidth(212)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(14, 18, 14, 16)
        sl.setSpacing(4)
        brand = QHBoxLayout()
        brand.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(draw_logo(30))
        brand.addWidget(logo)
        brand.addWidget(label("LexoPlay", "brand"))
        brand.addStretch(1)
        sl.addLayout(brand)
        sl.addSpacing(18)

        self.stack = QStackedWidget()
        self.pages: dict[str, QWidget] = {}
        self.games = GamesPage(ctl, self.decks)
        self.lexo = LexoPage(ctl, self.decks)
        self.keys = KeysPage(ctl)
        self.view = ViewPage(ctl)
        self.more = MorePage(ctl)
        for key, page in (("games", self.games), ("lexo", self.lexo), ("keys", self.keys), ("view", self.view), ("more", self.more)):
            self.pages[key] = page
            self.stack.addWidget(page)
        self.games.go_lexo.connect(lambda: self.show_page("lexo"))
        self.lexo.changed.connect(self._account_changed)

        self.nav = QButtonGroup(self)
        self.nav_buttons: dict[str, QPushButton] = {}
        for key, text, icon_name in self.PAGES:
            b = QPushButton(f"  {text}")
            b.setObjectName("nav")
            b.setCheckable(True)
            b.setIcon(icon(icon_name, theme.INK, 18))
            b.setIconSize(QSize(18, 18))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c=False, k=key: self.show_page(k))
            self.nav.addButton(b)
            self.nav_buttons[key] = b
            sl.addWidget(b)
        sl.addStretch(1)
        self.account_chip = label("", "muted", wrap=True)
        sl.addWidget(self.account_chip)
        sl.addWidget(label(f"Версия {__version__}", "version"))
        root.addWidget(side)
        root.addWidget(self.stack, 1)

        self.show_page("games")
        self.refresh_account()

    # --- навигация
    def show_page(self, key: str) -> None:
        self.stack.setCurrentWidget(self.pages[key])
        self.nav_buttons[key].setChecked(True)
        if key == "games":
            self.games.refresh()
        elif key == "lexo":
            self.lexo.refresh()
            if not self.decks.loaded:
                self.lexo.reload_decks()

    def open(self, page: str | None = None) -> None:
        if page:
            self.show_page(page)
        else:
            self.games.refresh()
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
        if self.ctl.account_status().linked and not self.decks.loaded:
            self.lexo.reload_decks()

    # --- обновления снаружи
    def refresh_games(self) -> None:
        if self.isVisible() and self.stack.currentWidget() is self.games:
            self.games.refresh()

    def refresh_account(self) -> None:
        st = self.ctl.account_status()
        if st.linked:
            text = f"Lexo: {st.label}"
            if st.offline:
                text += "\nнет связи"
            elif st.used is not None and st.limit:
                text += f"\nсегодня {st.used} из {st.limit}"
        else:
            text = "Lexo не привязан"
        self.account_chip.setText(text)
        if self.isVisible():
            current = self.stack.currentWidget()
            if current is self.lexo:
                self.lexo.refresh()
            elif current is self.games:
                self.games.refresh()

    def _account_changed(self) -> None:
        self.refresh_account()
        self.games.refresh()

    def show_message(self, text: str) -> None:
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.information(self, "LexoPlay", text)

    def closeEvent(self, e: QCloseEvent) -> None:  # noqa: N802
        e.ignore()
        self.hide()
        self.ctl.window_hidden()
