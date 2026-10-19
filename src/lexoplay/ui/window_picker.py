"""«Добавить игру»: список открытых окон, игрок выбирает своё.

Браузеры, лаунчеры и мессенджеры в список не попадают (games.NOT_GAMES), поэтому
случайно сделать игрой Chrome или Steam больше нельзя.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QFileInfo, QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog,
    QFileIconProvider,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lexoplay.games import WindowInfo, clean_title, rank
from lexoplay.ui.theme import app_icon, icon


def exe_icon(path: str) -> QIcon:
    if not path:
        return QIcon()
    try:
        return QFileIconProvider().icon(QFileInfo(path))
    except Exception:
        return QIcon()


class WindowPicker(QDialog):
    def __init__(self, list_windows: Callable[[], list[WindowInfo]], known: set[str], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Добавить игру")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(560, 460)
        self._list_windows = list_windows
        self._known = known
        self.chosen: WindowInfo | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(12)
        h = QLabel("Какое окно игры?")
        h.setObjectName("h2")
        root.addWidget(h)
        hint = QLabel(
            "Запусти игру, если она ещё не открыта, и выбери её окно. Браузеры, Steam и мессенджеры здесь не показываются."
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.list = QListWidget()
        self.list.setIconSize(QSize(32, 32))
        self.list.itemDoubleClicked.connect(lambda _i: self._accept())
        self.list.currentItemChanged.connect(lambda *_: self._sync())
        root.addWidget(self.list, 1)

        self.empty = QLabel("Открытых игр не видно. Запусти игру и нажми «Обновить».")
        self.empty.setObjectName("muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.empty)

        row = QHBoxLayout()
        refresh = QPushButton(" Обновить")
        refresh.setIcon(icon("refresh", size=16))
        refresh.clicked.connect(self.refresh)
        row.addWidget(refresh)
        row.addStretch(1)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        self.ok = QPushButton("Добавить")
        self.ok.setObjectName("primary")
        self.ok.setDefault(True)
        self.ok.clicked.connect(self._accept)
        row.addWidget(cancel)
        row.addWidget(self.ok)
        root.addLayout(row)
        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        try:
            windows = rank(self._list_windows())
        except Exception:
            windows = []
        for w in windows:
            title = clean_title(w.title, w.exe)
            state = []
            if w.exe in self._known:
                state.append("уже добавлена")
            if w.minimized:
                state.append("свёрнуто")
            elif w.w and w.h:
                state.append(f"{w.w}×{w.h}")
            item = QListWidgetItem(exe_icon(w.path), f"{title}\n{w.exe}" + (f" · {' · '.join(state)}" if state else ""))
            item.setData(Qt.ItemDataRole.UserRole, w)
            self.list.addItem(item)
        self.empty.setVisible(self.list.count() == 0)
        self.list.setVisible(self.list.count() > 0)
        if self.list.count():
            self.list.setCurrentRow(0)
        self._sync()

    def _sync(self) -> None:
        self.ok.setEnabled(self.list.currentItem() is not None)

    def _accept(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        self.chosen = item.data(Qt.ItemDataRole.UserRole)
        self.accept()
