"""Окно настроек: Аккаунт (привязка к Lexo), Игра, Дополнительно (свой ключ, сервер)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from lexoplay.config import Settings, get_api_key, set_api_key
from lexoplay.store import Store
from lexoplay.translate import LANGS
from lexoplay.ui.theme import app_icon

MAGPIE = [("auto", "Сам определяет Magpie"), ("always", "Игра всегда растянута на весь экран"), ("never", "Не растягивается")]
DECKS = [("game", "В колоду с названием игры"), ("current", "В текущую колоду в Lexo")]
MUTED = "color: #6E6C66;"
ERROR = "color: #C0392B;"


@dataclass(frozen=True, slots=True)
class AccountStatus:
    linked: bool
    label: str = ""  # @username
    details: str = ""  # «сегодня 12 из 50 переводов»
    queue: str = ""  # «2 слова ждут отправки»


class AccountController(Protocol):
    def account_status(self) -> AccountStatus: ...

    def link(self, code: str, done: Callable[[str | None], None]) -> None:
        """done(None) при успехе или done(текст ошибки). Вызывается в потоке Qt."""

    def unlink(self, done: Callable[[], None]) -> None: ...


class CodeEdit(QLineEdit):
    """Enter в поле кода привязывает ПК, а не закрывает окно настроек кнопкой «Сохранить»."""

    def keyPressEvent(self, e) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            e.accept()
            self.returnPressed.emit()
            return
        super().keyPressEvent(e)


def _muted(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet(MUTED)
    return lbl


class AccountTab(QWidget):
    def __init__(self, ctl: AccountController | None, settings: Settings) -> None:
        super().__init__()
        self._ctl = ctl
        self._settings = settings
        root = QVBoxLayout(self)
        root.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.title = QLabel()
        f = QFont()
        f.setPointSizeF(f.pointSizeF() * 1.25)
        f.setBold(True)
        self.title.setFont(f)
        root.addWidget(self.title)
        self.details = _muted("")
        root.addWidget(self.details)

        # не привязан
        self.link_box = QWidget()
        lb = QVBoxLayout(self.link_box)
        lb.setContentsMargins(0, 8, 0, 0)
        lb.addWidget(
            _muted(
                "Привяжи ПК к Lexo: перевод пойдёт через твой аккаунт, без своего ключа, "
                "а слова из игры будут сохраняться в карточки.\n\n"
                "Код можно взять в боте командой /link или в Mini App: Профиль → Подключить ПК."
            )
        )
        row = QHBoxLayout()
        self.code = CodeEdit()
        self.code.setPlaceholderText("Код, например K7QX2M")
        self.code.setMaxLength(16)
        self.code.setFont(QFont("Consolas", 14))
        self.code.textEdited.connect(lambda t: self.code.setText(t.upper()))
        self.code.returnPressed.connect(self._link)
        self.link_btn = QPushButton("Привязать")
        self.link_btn.setDefault(False)
        self.link_btn.setAutoDefault(False)
        self.link_btn.clicked.connect(self._link)
        row.addWidget(self.code, 1)
        row.addWidget(self.link_btn)
        lb.addLayout(row)
        root.addWidget(self.link_box)

        # привязан
        self.linked_box = QWidget()
        kb = QFormLayout(self.linked_box)
        kb.setContentsMargins(0, 8, 0, 0)
        self.deck = QComboBox()
        for code, name in DECKS:
            self.deck.addItem(name, code)
        self.deck.setCurrentIndex(max(0, self.deck.findData(settings.deck)))
        kb.addRow("Слова сохранять", self.deck)
        self.unlink_btn = QPushButton("Отвязать этот ПК")
        self.unlink_btn.setAutoDefault(False)
        self.unlink_btn.clicked.connect(self._unlink)
        kb.addRow("", self.unlink_btn)
        root.addWidget(self.linked_box)

        self.status = QLabel()
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.refresh()

    def refresh(self) -> None:
        st = self._ctl.account_status() if self._ctl else AccountStatus(False)
        self.title.setText(f"Lexo: {st.label}" if st.linked else "Lexo не привязан")
        extra = [x for x in (st.details, st.queue) if x]
        self.details.setText(" · ".join(extra))
        self.details.setVisible(bool(extra))
        self.link_box.setVisible(not st.linked)
        self.linked_box.setVisible(st.linked)

    def _link(self) -> None:
        code = self.code.text().strip().replace(" ", "")
        if len(code) < 4 or self._ctl is None:
            self._show("Введи код из бота.", error=True)
            return
        self.link_btn.setEnabled(False)
        self._show("Привязываю…")

        def done(err: str | None) -> None:
            self.link_btn.setEnabled(True)
            if err:
                self._show(err, error=True)
            else:
                self.code.clear()
                self._show("Готово. Теперь F2 переводит через Lexo, а F3 сохраняет слова в карточки.")
                self.refresh()

        self._ctl.link(code, done)

    def _unlink(self) -> None:
        if self._ctl is None:
            return
        self.unlink_btn.setEnabled(False)

        def done() -> None:
            self.unlink_btn.setEnabled(True)
            self._show("ПК отвязан.")
            self.refresh()

        self._ctl.unlink(done)

    def _show(self, text: str, *, error: bool = False) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(ERROR if error else MUTED)

    def apply(self, s: Settings) -> None:
        s.deck = self.deck.currentData()


class SettingsDialog(QDialog):
    def __init__(
        self,
        settings: Settings,
        store: Store,
        parent: QWidget | None = None,
        *,
        account: AccountController | None = None,
        tab: str = "account",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("LexoPlay: настройки")
        self.setWindowIcon(app_icon())
        self.setMinimumWidth(560)
        self._settings = settings
        self._store = store

        self.tabs = QTabWidget()
        self.account = AccountTab(account, settings)
        self.tabs.addTab(self.account, "Аккаунт")
        self.tabs.addTab(self._game_tab(settings, store), "Игра")
        self.tabs.addTab(self._extra_tab(settings), "Дополнительно")
        self.tabs.setCurrentIndex({"account": 0, "game": 1, "extra": 2}.get(tab, 0))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addWidget(self.tabs)
        root.addWidget(buttons)
        if tab == "account":
            self.account.code.setFocus()

    def _game_tab(self, settings: Settings, store: Store) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.magpie = QComboBox()
        for code, name in MAGPIE:
            self.magpie.addItem(name, code)
        self.magpie.setCurrentIndex(max(0, self.magpie.findData(settings.magpie_mode)))
        form.addRow("Растягивание", self.magpie)

        self.font_scale = QDoubleSpinBox()
        self.font_scale.setRange(0.7, 1.8)
        self.font_scale.setSingleStep(0.1)
        self.font_scale.setValue(settings.font_scale)
        form.addRow("Размер текста", self.font_scale)

        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(40, 100)
        self.opacity.setValue(round(settings.strip_opacity * 100))
        form.addRow("Плотность фона строки", self.opacity)

        hk = settings.hotkeys
        hk_label = QLabel(
            f"{hk.translate} перевод · {hk.words} слова в Lexo · {hk.hide} скрыть · "
            f"{hk.reselect} выделить область заново · {hk.add_game} добавить игру (в любом окне)"
        )
        hk_label.setWordWrap(True)
        form.addRow("Клавиши", hk_label)
        form.addRow("", _muted("Клавиши меняются в settings.json (Папка с логами в меню трея)."))

        self.games = QListWidget()
        for g in store.list_games():
            item = QListWidgetItem(f"{g.title}  ({g.exe})")
            item.setData(Qt.ItemDataRole.UserRole, g.exe)
            self.games.addItem(item)
        remove = QPushButton("Забыть игру")
        remove.setAutoDefault(False)
        remove.clicked.connect(self._remove_game)
        games_box = QVBoxLayout()
        games_box.addWidget(self.games)
        games_box.addWidget(remove, 0, Qt.AlignmentFlag.AlignRight)
        form.addRow("Игры", games_box)
        return w

    def _extra_tab(self, settings: Settings) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.addRow(
            "",
            _muted(
                "Без привязки к Lexo можно переводить своим ключом Anthropic. "
                "Слова в карточки так не сохраняются. Если ПК привязан, ключ не используется."
            ),
        )
        self.key = QLineEdit(get_api_key() or "")
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("sk-ant-...")
        show = QPushButton("Показать")
        show.setCheckable(True)
        show.setAutoDefault(False)
        show.toggled.connect(
            lambda on: self.key.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password)
        )
        key_row = QHBoxLayout()
        key_row.addWidget(self.key, 1)
        key_row.addWidget(show)
        form.addRow("Ключ Anthropic", key_row)

        self.model = QLineEdit(settings.model)
        form.addRow("Модель", self.model)

        self.lang = QComboBox()
        for code, name in LANGS.items():
            if code != "en":
                self.lang.addItem(f"{name} ({code})", code)
        self.lang.setCurrentIndex(max(0, self.lang.findData(settings.target_lang)))
        form.addRow("Переводить на", self.lang)
        form.addRow("", _muted("С Lexo язык перевода берётся из твоего профиля в Lexo."))

        self.server = QLineEdit(settings.server_url)
        form.addRow("Сервер Lexo", self.server)

        self.debug = QCheckBox("Сохранять кадры и OCR в папку debug")
        self.debug.setChecked(settings.debug)
        form.addRow("Отладка", self.debug)
        return w

    def _remove_game(self) -> None:
        item = self.games.currentItem()
        if item is None:
            return
        self._store.delete_game(item.data(Qt.ItemDataRole.UserRole))
        self.games.takeItem(self.games.row(item))

    def _save(self) -> None:
        s = self._settings
        self.account.apply(s)
        s.model = self.model.text().strip() or s.model
        s.target_lang = self.lang.currentData()
        s.server_url = self.server.text().strip().rstrip("/") or s.server_url
        s.magpie_mode = self.magpie.currentData()
        s.font_scale = float(self.font_scale.value())
        s.strip_opacity = self.opacity.value() / 100
        s.debug = self.debug.isChecked()
        s.save()
        set_api_key(self.key.text().strip())
        self.accept()
