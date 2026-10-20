"""Главное окно LexoPlay: Игры, История, Слова, Прогресс, Lexo, Клавиши, Вид, Ещё.

Настройки применяются сразу, без кнопки «Сохранить». Закрытие окна прячет его в трей:
LexoPlay продолжает работать, пока его не закроют через меню трея.
Вся логика в контроллере (App); окно только показывает и передаёт действия.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QDesktopServices,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QImage,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QButtonGroup,
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
    QSizePolicy,
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
from lexoplay.store import Game, SavedWord, Store, StoredLine
from lexoplay.ui import theme
from lexoplay.ui.hotkey_edit import HotkeyEdit
from lexoplay.ui.history_page import HistoryPage
from lexoplay.ui.pages import GlossaryDialog, WordsPage
from lexoplay.ui.progress_page import ProgressPage
from lexoplay.ui.theme import app_icon, draw_logo, icon

RELEASES_URL = "https://github.com/Satoros1/lexoplay/releases/latest"


@dataclass
class UpdateState:
    """Проверка и установка новой версии. phase: idle | checking | latest | available | downloading | installing | error."""

    phase: str = "idle"
    release: object | None = None  # updater.Release
    progress: int = 0
    error: str | None = None
    checked_at: float = 0.0


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
    own_key: bool = False  # без Lexo, но со своим ключом Anthropic


@dataclass
class DeckState:
    decks: list[Deck] = field(default_factory=list)
    current_id: int | None = None
    loaded: bool = False
    error: str | None = None


class Controller(Protocol):
    settings: Settings
    store: Store
    update: UpdateState

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
    def usage_summary(self) -> str: ...
    def check_key(self, key: str, done: Callable[[str | None], None]) -> None: ...
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
    def get_last_read(self, exe: str): ...  # type: ignore[no-untyped-def]
    def autostart_supported(self) -> bool: ...
    def autostart_enabled(self) -> bool: ...
    def set_autostart(self, on: bool) -> bool: ...
    def check_updates(self, *, manual: bool = True) -> None: ...
    def install_update(self) -> None: ...
    def progress(self): ...  # type: ignore[no-untyped-def]


from lexoplay.ui.widgets import (  # noqa: E402,F401
    Tile,
    Toggle,
    ValueSlider,
    button,
    card,
    card_list,
    clear,
    keycap,
    label,
    page_shell,
    setting_row,
    tile_color,
)


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
            (hk.words, "подчеркнуть слова: клик по слову, потом «+» или Enter сохраняет его. Shift+клик выбирает фразу. "
             "Там же «Перевести иначе», если перевод не понравился"),
            (hk.hide, "спрятать перевод"),
            (hk.explain, "разбор реплики: сленг, идиомы и трудные места, их можно сохранить в слова"),
            (hk.reselect, "выделить окно диалога заново и поставить плашку перевода, куда удобно"),
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
        name = label(g.title, "title", wrap=True)  # длинное название переносится, а не растягивает окно
        name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        top.addWidget(name, 1)
        if active:
            top.addWidget(label("сейчас в игре", "chip"), 0, Qt.AlignmentFlag.AlignTop)
        col.addLayout(top)
        if g.region is not None:
            state = label(f"{g.exe} · окно диалога выделено", "muted")
        else:
            state = label(f"{g.exe} · окно диалога ещё не выделено: открой игру и нажми {self.ctl.settings.hotkeys.translate}", "warn")
        state.setWordWrap(True)
        col.addWidget(state)
        if g.region is not None and g.name_region is None and self._names_missing(g.exe):
            tip = label(
                "Совет: имя говорящего почти не читается. Если в игре оно в отдельной рамке, нажми «Выделить окно "
                "диалога», потом Tab и обведи имя. Так перевод лучше понимает, кто говорит, и не путает лицо и род.",
                "version",
                wrap=True,
            )
            col.addWidget(tip)
        read = self.ctl.get_last_read(g.exe) if g.region is not None else None
        if read is not None:
            col.addLayout(self._last_read(read))
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
        gloss = QAction("Имена и термины…", menu)
        gloss.triggered.connect(lambda: self._glossary(g))
        forget = QAction("Убрать из списка", menu)
        forget.triggered.connect(lambda: self._forget(g))
        menu.addAction(rename)
        menu.addAction(gloss)
        if g.ocr_mode:
            reocr = QAction("Подобрать распознавание заново", menu)
            reocr.triggered.connect(lambda: self._reset_ocr(g))
            menu.addAction(reocr)
        menu.addSeparator()
        menu.addAction(forget)
        more.setMenu(menu)
        row.addWidget(more, 0, Qt.AlignmentFlag.AlignTop)
        return frame

    def _names_missing(self, exe: str) -> bool:
        """У последних реплик игры почти нет имени говорящего."""
        try:
            named, total = self.ctl.store.speaker_share(exe)
        except Exception:
            return False
        return total >= 15 and named <= total * 0.1

    def _last_read(self, read) -> QHBoxLayout:  # type: ignore[no-untyped-def]
        """Что LexoPlay последний раз прочитал в окне диалога: сразу видно, туда ли выделено."""
        row = QHBoxLayout()
        row.setSpacing(10)
        img = read.image.convert("RGB")
        qimg = QImage(img.tobytes("raw", "RGB"), img.width, img.height, img.width * 3, QImage.Format.Format_RGB888).copy()
        pix = QPixmap.fromImage(qimg).scaled(
            150, 46, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
        shot = QLabel()
        shot.setObjectName("preview")
        shot.setPixmap(pix)
        shot.setToolTip("Последний кадр окна диалога")
        row.addWidget(shot, 0, Qt.AlignmentFlag.AlignTop)
        text = read.text.strip() or "текста не нашлось"
        if len(text) > 140:
            text = text[:140].rstrip() + "…"
        col = QVBoxLayout()
        col.setSpacing(1)
        col.addWidget(label(f"Прочитано: {text}", "muted", wrap=True))
        col.addWidget(label(f"Распознавание: {read.mode_name}, подобрано само", "version", wrap=True))
        row.addLayout(col, 1)
        return row

    def _glossary(self, g: Game) -> None:
        GlossaryDialog(self.ctl, g, self.window()).exec()

    def _reset_ocr(self, g: Game) -> None:
        fresh = self.ctl.store.get_game(g.exe) or g
        self.ctl.save_game(fresh.with_(ocr_mode=None, binarize=False))
        self.refresh()

    def _deck_combo(self, g: Game) -> QComboBox:
        combo = QComboBox()
        # ширина по названию, но не шире 340: иначе карточка игры вылезает за окно
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(18)
        combo.setMaximumWidth(340)
        combo.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        own = f"Своя колода «{g.title}»" if len(g.title) <= 18 else "Своя колода игры"
        combo.addItem(own, ("game", None, None))
        combo.setItemData(0, f"Колода «{g.title}» создастся сама с первым словом", Qt.ItemDataRole.ToolTipRole)
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
        sf = QFont(self.settings.strip_font or theme.FONT_FAMILY)
        sf.setPixelSize(round(px))
        sf.setWeight(QFont.Weight.Bold if self.settings.strip_bold else QFont.Weight.Medium)
        fm = QFontMetricsF(sf)
        text = "Знаешь, мне совсем не хотелось сюда сегодня приходить."
        inner_w = dlg.width() - 2 * px * 0.9
        th = fm.boundingRect(QRectF(0, 0, inner_w, 1000), int(Qt.TextFlag.TextWordWrap), text).height()
        original = "I was reluctant to come here tonight, you know."
        of = QFont(self.settings.strip_font or theme.FONT_FAMILY)
        of.setPixelSize(round(max(12.0, px * 0.78)))
        oh = 0.0
        if self.settings.show_original:
            oh = QFontMetricsF(of).boundingRect(QRectF(0, 0, inner_w, 1000), int(Qt.TextFlag.TextWordWrap), original).height()
            oh += px * 0.35
        strip_h = th + oh + px * 1.2
        strip = QRectF(dlg.x(), dlg.y() - 8 - strip_h, dlg.width(), strip_h)
        bg = QColor(theme.STRIP_BG)
        bg.setAlphaF(max(0.3, min(1.0, self.settings.strip_opacity)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(strip, min(10.0, strip.height() / 2), min(10.0, strip.height() / 2))
        top = strip.y() + px * 0.6
        if oh:
            p.setFont(of)
            p.setPen(QColor("#9A988F"))
            p.drawText(QRectF(strip.x() + px * 0.9, top, inner_w, oh), int(Qt.TextFlag.TextWordWrap), original)
            top += oh
        p.setFont(sf)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(QRectF(strip.x() + px * 0.9, top, inner_w, th + 2), int(Qt.TextFlag.TextWordWrap), text)
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
        box, bl = card_list()
        prev_wrap = QWidget()
        pw = QVBoxLayout(prev_wrap)
        pw.setContentsMargins(16, 16, 16, 6)
        self.preview = StripPreview(s)
        pw.addWidget(self.preview)
        bl.addWidget(prev_wrap)
        self.size_ctl = ValueSlider(70, 180, round(s.font_scale * 100))
        self.opacity_ctl = ValueSlider(40, 100, round(s.strip_opacity * 100))
        self.size, self.opacity = self.size_ctl.slider, self.opacity_ctl.slider
        self.size_val, self.opacity_val = self.size_ctl.value, self.opacity_ctl.value
        self.original = Toggle(s.show_original)
        self.place = QComboBox()
        for code, text in (("above", "Над окном диалога"), ("below", "Под окном диалога"), ("top", "Вверху экрана")):
            self.place.addItem(text, code)
        self.place.setCurrentIndex(max(0, self.place.findData(s.strip_place)))
        self.place.setMinimumWidth(220)
        self.place.activated.connect(self._place)
        font_ctl = QWidget()
        fl = QHBoxLayout(font_ctl)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(10)
        self.font = QComboBox()
        self.font.addItem("Как в окне LexoPlay", "")
        families = set(QFontDatabase.families())
        for fam in ("Segoe UI", "Arial", "Calibri", "Verdana", "Tahoma", "Trebuchet MS", "Bahnschrift", "Georgia", "Comic Sans MS", "Consolas"):
            if fam in families or fam == s.strip_font:
                self.font.addItem(fam, fam)
        self.font.setCurrentIndex(max(0, self.font.findData(s.strip_font)))
        self.font.setMinimumWidth(200)
        self.font.activated.connect(self._font)
        self.bold = Toggle(s.strip_bold)
        self.bold.toggled.connect(self._bold)
        fl.addWidget(self.font)
        fl.addWidget(label("жирный", "muted"))
        fl.addWidget(self.bold)
        bl.addWidget(setting_row("Размер текста", "Насколько крупно перевод поверх игры", self.size_ctl))
        bl.addWidget(setting_row("Плотность фона", "Чем выше, тем меньше игра просвечивает сквозь плашку", self.opacity_ctl))
        bl.addWidget(
            setting_row(
                "Где показывать",
                f"Для отдельной игры можно своё место: в игре нажми {s.hotkeys.reselect} и перетащи плашку перевода",
                self.place,
            )
        )
        bl.addWidget(setting_row("Шрифт перевода", "", font_ctl))
        bl.addWidget(
            setting_row(
                "Английский оригинал над переводом",
                "Мелким шрифтом: видно, что именно переведено, и легче учить слова",
                self.original,
                last=True,
            )
        )
        self.original.toggled.connect(self._original)
        lay.addWidget(box)

        lay.addWidget(label("АВТОПЕРЕВОД", "section"))
        auto_box, al = card_list()
        self.auto = Toggle(s.auto_translate)
        self.auto.toggled.connect(self._auto)
        hk = s.hotkeys
        al.addWidget(
            setting_row(
                "Переводить следующую реплику автоматически",
                f"Нажми {hk.translate} один раз: дальше, когда пропускаешь реплику, следующая переводится сама. "
                f"{hk.hide} останавливает. Каждая новая реплика это запрос к модели, повторы из истории бесплатно. "
                "Включается и в меню значка в трее.",
                self.auto,
                last=True,
            )
        )
        lay.addWidget(auto_box)
        self.size.valueChanged.connect(self._sliders)
        self.opacity.valueChanged.connect(self._sliders)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(250)
        self._save_timer.timeout.connect(self.ctl.settings_changed)

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

    def _sliders(self) -> None:
        self.ctl.settings.font_scale = self.size.value() / 100
        self.ctl.settings.strip_opacity = self.opacity.value() / 100
        self.preview.update()
        self._save_timer.start()

    def _place(self) -> None:
        self.ctl.settings.strip_place = self.place.currentData()
        self.ctl.settings_changed()

    def _font(self) -> None:
        self.ctl.settings.strip_font = self.font.currentData() or ""
        self.preview.update()
        self.ctl.settings_changed()

    def _bold(self, on: bool) -> None:
        self.ctl.settings.strip_bold = on
        self.preview.update()
        self.ctl.settings_changed()

    def _original(self, on: bool) -> None:
        self.ctl.settings.show_original = on
        self.preview.update()
        self.ctl.settings_changed()

    def _auto(self, on: bool) -> None:
        if self.ctl.settings.auto_translate != on:
            self.ctl.settings.auto_translate = on
            self.ctl.settings_changed()

    def refresh(self) -> None:
        self.auto.blockSignals(True)
        self.auto.setChecked(self.ctl.settings.auto_translate)
        self.auto.blockSignals(False)

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
        scroll, lay, _actions = page_shell("Ещё", "Свой ключ, запуск, обновления и помощь, если что-то не работает.")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        lay.addWidget(label("СВОЙ КЛЮЧ ANTHROPIC", "section"))
        box, bl = card()
        bl.addWidget(
            label(
                "Без привязки к Lexo программа переводит реплики и слова твоим ключом Anthropic. Слова копятся во вкладке "
                "«Слова» и сами уйдут в Lexo, когда привяжешь ПК. Если ПК привязан, ключ не используется.",
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
        self.check_btn = button("Проверить")
        self.check_btn.clicked.connect(self._check_key)
        self.model = QComboBox()
        self.model.setEditable(True)
        for m in ("claude-haiku-5-5", "claude-sonnet-5-5"):
            self.model.addItem(m)
        if self.model.findText(s.model) < 0:
            self.model.addItem(s.model)
        self.model.setCurrentText(s.model)
        self.model.activated.connect(lambda _i: self._model())
        self.model.lineEdit().editingFinished.connect(self._model)
        form.addWidget(label("Ключ"), 0, 0)
        form.addWidget(self.key, 0, 1)
        form.addWidget(show, 0, 2)
        form.addWidget(self.check_btn, 0, 3)
        form.addWidget(label("Модель"), 1, 0)
        form.addWidget(self.model, 1, 1, 1, 3)
        bl.addLayout(form)
        bl.addWidget(
            label(
                "claude-haiku-5-5: быстро и дёшево, сотня реплик стоит пару центов. claude-sonnet-5-5: переводит "
                "тоньше и лучше держит характер персонажей, но примерно в 20 раз дороже. Кнопка «Перевести иначе» "
                "в режиме слов (F3) всегда переводит одну реплику через claude-sonnet-5-5.",
                "version",
                wrap=True,
            )
        )
        self.key_status = label("", "muted", wrap=True)
        self.key_status.hide()
        bl.addWidget(self.key_status)
        self.usage = label("", "muted", wrap=True)
        bl.addWidget(self.usage)
        lay.addWidget(box)

        lay.addWidget(label("ЗАПУСК И ОБНОВЛЕНИЯ", "section"))
        box4, b4 = card_list()
        supported = ctl.autostart_supported()
        self.autostart = Toggle(supported and ctl.autostart_enabled())
        self.autostart.setEnabled(supported)
        self.autostart.toggled.connect(self._autostart)
        b4.addWidget(
            setting_row(
                "Запускать вместе с Windows",
                "Сразу в трей, без окна: в игре клавиши уже работают" if supported else "Работает в LexoPlay.exe",
                self.autostart,
            )
        )
        self.auto_updates = Toggle(s.check_updates)
        self.auto_updates.toggled.connect(self._auto_updates)
        b4.addWidget(setting_row("Проверять новые версии", "Раз в несколько часов, ставится одной кнопкой", self.auto_updates))
        upd_row = QFrame()
        upd_row.setObjectName("rowLast")
        ul = QVBoxLayout(upd_row)
        ul.setContentsMargins(18, 14, 18, 14)
        ul.setSpacing(8)
        upd = QHBoxLayout()
        upd.setSpacing(10)
        vcol = QVBoxLayout()
        vcol.setSpacing(2)
        self.version = label("", "title")
        vcol.addWidget(self.version)
        self.update_note = label("", "muted")
        vcol.addWidget(self.update_note)
        upd.addLayout(vcol, 1)
        notes = button("Что нового", "link")
        notes.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://github.com/Satoros1/lexoplay/releases")))
        upd.addWidget(notes, 0, Qt.AlignmentFlag.AlignVCenter)
        upd.addSpacing(8)
        self.update_btn = button("Проверить")
        self.update_btn.clicked.connect(self._update_clicked)
        upd.addWidget(self.update_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        ul.addLayout(upd)
        self.update_bar = QProgressBar()
        self.update_bar.setTextVisible(False)
        self.update_bar.setRange(0, 100)
        self.update_bar.hide()
        ul.addWidget(self.update_bar)
        b4.addWidget(upd_row)
        lay.addWidget(box4)

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
        box3, b3 = card_list()
        self.debug = Toggle(s.debug)
        self.debug.toggled.connect(self._debug)
        b3.addWidget(
            setting_row(
                "Режим отладки",
                "Сохранять кадры окна диалога и распознанный текст: пригодится, если что-то читается не так",
                self.debug,
            )
        )
        help_row = QFrame()
        help_row.setObjectName("rowLast")
        hl = QVBoxLayout(help_row)
        hl.setContentsMargins(18, 14, 18, 14)
        btns = QHBoxLayout()
        report = button(" Собрать отчёт об ошибке", None, "send")
        report.clicked.connect(self._report)
        logs = button(" Папка с логами", None, "folder")
        logs.clicked.connect(self.ctl.open_logs)
        btns.addWidget(report)
        btns.addWidget(logs)
        btns.addStretch(1)
        hl.addLayout(btns)
        b3.addWidget(help_row)
        b3 = hl  # заметка об отчёте под кнопками
        self.report_note = label("", "muted", wrap=True)
        self.report_note.hide()
        b3.addWidget(self.report_note)
        lay.addWidget(box3)
        lay.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        linked = self.ctl.account_status().linked
        self.usage.setText("Сейчас перевод идёт через Lexo." if linked else self.ctl.usage_summary())
        self.refresh_update()

    def refresh_update(self) -> None:
        u = self.ctl.update
        rel = u.release
        self.version.setText(f"LexoPlay {__version__}")
        self.update_bar.setVisible(u.phase == "downloading")
        self.update_bar.setValue(u.progress)
        self.update_btn.setEnabled(u.phase not in ("checking", "downloading", "installing"))
        note, btn = "", "Проверить"
        if u.phase == "checking":
            note = "проверяю…"
        elif u.phase == "latest":
            note = "последняя версия"
        elif u.phase == "available" and rel is not None:
            note, btn = f"вышла версия {rel.version}", "Обновить"  # type: ignore[attr-defined]
        elif u.phase == "downloading":
            note, btn = f"скачиваю обновление… {u.progress}%", "Обновить"
        elif u.phase == "installing":
            note = "устанавливаю, LexoPlay сейчас перезапустится"
        elif u.phase == "error":
            note, btn = u.error or "обновление не получилось", "Ещё раз" if rel is not None else "Проверить"
        self.update_note.setText(note)
        self.update_note.setObjectName("error" if u.phase == "error" else ("ok" if u.phase == "available" else "muted"))
        self.update_note.style().unpolish(self.update_note)
        self.update_note.style().polish(self.update_note)
        self.update_note.setWordWrap(u.phase == "error")
        self.update_btn.setText(btn)
        self.update_btn.setObjectName("primary" if btn in ("Обновить", "Ещё раз") else "")
        self.update_btn.style().unpolish(self.update_btn)
        self.update_btn.style().polish(self.update_btn)

    def _update_clicked(self) -> None:
        if self.ctl.update.release is not None:
            self.ctl.install_update()
        else:
            self.ctl.check_updates(manual=True)

    def _check_key(self) -> None:
        key = self.key.text().strip()
        if not key:
            self._key_status("Сначала вставь ключ.", error=True)
            return
        self.ctl.set_api_key(key)
        self.check_btn.setEnabled(False)
        self._key_status("Проверяю…")

        def done(err: str | None) -> None:
            self.check_btn.setEnabled(True)
            if err:
                self._key_status(err, error=True)
            else:
                self._key_status(f"Ключ работает, модель {self.ctl.settings.model} отвечает.", ok=True)

        self.ctl.check_key(key, done)

    def _key_status(self, text: str, *, error: bool = False, ok: bool = False) -> None:
        self.key_status.setText(text)
        self.key_status.setObjectName("error" if error else ("ok" if ok else "muted"))
        self.key_status.style().unpolish(self.key_status)
        self.key_status.style().polish(self.key_status)
        self.key_status.show()

    def _autostart(self, on: bool) -> None:
        if not self.ctl.set_autostart(on):
            self.autostart.blockSignals(True)
            self.autostart.setChecked(not on)
            self.autostart.blockSignals(False)

    def _auto_updates(self, on: bool) -> None:
        self.ctl.settings.check_updates = on
        self.ctl.settings_changed()

    def _model(self) -> None:
        model = self.model.currentText().strip()
        if model and model != self.ctl.settings.model:
            self.ctl.settings.model = model
            self.ctl.settings_changed()
            self.usage.setText(self.ctl.usage_summary())

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
class UpdateBar(QFrame):
    """Полоска сверху на любой вкладке: вышла новая версия, обновить одним нажатием."""

    def __init__(self, ctl: Controller) -> None:
        super().__init__()
        self.ctl = ctl
        self.setObjectName("updateBar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(32, 8, 24, 8)
        lay.setSpacing(12)
        self.text = label("", "title", wrap=True)
        lay.addWidget(self.text, 1)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 100)
        self.bar.setFixedWidth(160)
        lay.addWidget(self.bar)
        self.btn = button("Обновить", "primary")
        self.btn.clicked.connect(self.ctl.install_update)
        lay.addWidget(self.btn)
        self.refresh()

    def refresh(self) -> None:
        u = self.ctl.update
        rel = u.release
        show = rel is not None and u.phase in ("available", "downloading", "installing", "error")
        self.setVisible(show)
        if not show:
            return
        version = getattr(rel, "version", "")
        self.bar.setVisible(u.phase == "downloading")
        self.bar.setValue(u.progress)
        self.btn.setVisible(u.phase in ("available", "error"))
        self.btn.setText("Ещё раз" if u.phase == "error" else "Обновить")
        if u.phase == "downloading":
            self.text.setText(f"Скачиваю LexoPlay {version}…")
        elif u.phase == "installing":
            self.text.setText("Устанавливаю, LexoPlay сейчас перезапустится")
        elif u.phase == "error":
            self.text.setText(u.error or "Обновление не получилось")
        else:
            self.text.setText(f"Вышла новая версия LexoPlay {version}. Обновится одним нажатием, настройки останутся.")


class MainWindow(QWidget):
    PAGES = (
        ("games", "Игры", "games"),
        ("history", "История", "history"),
        ("words", "Слова", "words"),
        ("progress", "Прогресс", "progress"),
        ("lexo", "Lexo", "lexo"),
        ("keys", "Клавиши", "keys"),
        ("view", "Вид", "view"),
        ("more", "Ещё", "more"),
    )

    def __init__(self, ctl: Controller) -> None:
        super().__init__()
        self.ctl = ctl
        self.setObjectName("root")
        self.setWindowTitle("LexoPlay")
        self.setWindowIcon(app_icon())
        self.resize(1000, 700)
        self.setMinimumSize(840, 580)
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
        self.history = HistoryPage(ctl)
        self.words = WordsPage(ctl, lambda: self.ctl.account_status().linked, lambda: self.ctl.settings.hotkeys.words)
        self.progress = ProgressPage(ctl)
        self.lexo = LexoPage(ctl, self.decks)
        self.keys = KeysPage(ctl)
        self.view = ViewPage(ctl)
        self.more = MorePage(ctl)
        for key, page in (
            ("games", self.games),
            ("history", self.history),
            ("words", self.words),
            ("progress", self.progress),
            ("lexo", self.lexo),
            ("keys", self.keys),
            ("view", self.view),
            ("more", self.more),
        ):
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

        main = QVBoxLayout()
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        self.update_bar = UpdateBar(ctl)
        main.addWidget(self.update_bar)
        main.addWidget(self.stack, 1)
        root.addLayout(main, 1)

        self.show_page("games")
        self.refresh_account()

    # --- навигация
    def show_page(self, key: str) -> None:
        self.stack.setCurrentWidget(self.pages[key])
        self.nav_buttons[key].setChecked(True)
        if key == "games":
            self.games.refresh()
        elif key == "history":
            self.history.refresh()
        elif key == "words":
            self.words.refresh()
        elif key == "progress":
            self.progress.refresh()
        elif key == "lexo":
            self.lexo.refresh()
            if not self.decks.loaded:
                self.lexo.reload_decks()
        elif key == "more":
            self.more.refresh()

    def open(self, page: str | None = None) -> None:
        if page:
            self.show_page(page)
        else:
            current = next((k for k, w in self.pages.items() if w is self.stack.currentWidget()), "games")
            self.show_page(current)
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

    def refresh_update(self) -> None:
        self.update_bar.refresh()
        self.more.refresh_update()

    def refresh_settings(self) -> None:
        self.view.refresh()

    def refresh_account(self) -> None:
        st = self.ctl.account_status()
        if st.linked:
            text = f"Lexo: {st.label}"
            if st.offline:
                text += "\nнет связи"
            elif st.used is not None and st.limit:
                text += f"\nсегодня {st.used} из {st.limit}"
        elif st.own_key:
            text = "Lexo не привязан\nперевод своим ключом"
        else:
            text = "Lexo не привязан"
        self.account_chip.setText(text)
        if self.isVisible():
            current = self.stack.currentWidget()
            if current is self.lexo:
                self.lexo.refresh()
            elif current is self.games:
                self.games.refresh()
            elif current is self.words:
                self.words.refresh()
            elif current is self.more:
                self.more.refresh()

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
