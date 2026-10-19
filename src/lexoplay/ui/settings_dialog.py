"""Окно настроек (экран 4 на канвасе, урезанный до MVP)."""

from __future__ import annotations

from PySide6.QtCore import Qt
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
    QVBoxLayout,
    QWidget,
)

from lexoplay.config import Settings, get_api_key, set_api_key
from lexoplay.store import Store
from lexoplay.translate import LANGS
from lexoplay.ui.theme import app_icon

MAGPIE = [("auto", "Сам определяет Magpie"), ("always", "Игра всегда растянута на весь экран"), ("never", "Не растягивается")]


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, store: Store, parent: QWidget | None = None, *, focus_key: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle("LexoPlay: настройки")
        self.setWindowIcon(app_icon())
        self.setMinimumWidth(520)
        self._settings = settings
        self._store = store

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.key = QLineEdit(get_api_key() or "")
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("sk-ant-...")
        show = QPushButton("Показать")
        show.setCheckable(True)
        show.toggled.connect(
            lambda on: self.key.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password)
        )
        key_row = QHBoxLayout()
        key_row.addWidget(self.key, 1)
        key_row.addWidget(show)
        form.addRow("Ключ Anthropic", key_row)
        hint = QLabel("Пока перевод идёт напрямую через твой ключ. Со связкой с Lexo ключ станет не нужен.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #6E6C66;")
        form.addRow("", hint)

        self.model = QLineEdit(settings.model)
        form.addRow("Модель", self.model)

        self.lang = QComboBox()
        for code, name in LANGS.items():
            if code != "en":
                self.lang.addItem(f"{name} ({code})", code)
        self.lang.setCurrentIndex(max(0, self.lang.findData(settings.target_lang)))
        form.addRow("Переводить на", self.lang)

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

        self.debug = QCheckBox("Сохранять кадры и OCR в папку debug")
        self.debug.setChecked(settings.debug)
        form.addRow("Отладка", self.debug)

        hk = settings.hotkeys
        hk_label = QLabel(
            f"{hk.translate} перевод · {hk.hide} скрыть · {hk.reselect} выделить область заново · "
            f"{hk.add_game} добавить игру (в любом окне)"
        )
        hk_label.setWordWrap(True)
        form.addRow("Клавиши", hk_label)

        self.games = QListWidget()
        for g in store.list_games():
            item = QListWidgetItem(f"{g.title}  ({g.exe})")
            item.setData(Qt.ItemDataRole.UserRole, g.exe)
            self.games.addItem(item)
        remove = QPushButton("Забыть игру")
        remove.clicked.connect(self._remove_game)
        games_box = QVBoxLayout()
        games_box.addWidget(self.games)
        games_box.addWidget(remove, 0, Qt.AlignmentFlag.AlignRight)
        form.addRow("Игры", games_box)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(buttons)
        if focus_key:
            self.key.setFocus()

    def _remove_game(self) -> None:
        item = self.games.currentItem()
        if item is None:
            return
        self._store.delete_game(item.data(Qt.ItemDataRole.UserRole))
        self.games.takeItem(self.games.row(item))

    def _save(self) -> None:
        s = self._settings
        s.model = self.model.text().strip() or s.model
        s.target_lang = self.lang.currentData()
        s.magpie_mode = self.magpie.currentData()
        s.font_scale = float(self.font_scale.value())
        s.strip_opacity = self.opacity.value() / 100
        s.debug = self.debug.isChecked()
        s.save()
        set_api_key(self.key.text().strip())
        self.accept()
