"""Сборка мусора только в спокойный момент главного цикла.

Автоматический сборщик Python срабатывает где попало, в том числе внутри обработчика системных
сообщений Windows (nativeEventFilter). Если в этот момент он удаляет виджет Qt из цикла ссылок,
Qt роняет процесс («Fatal Python error: Aborted»). Поэтому автосборку выключаем и собираем
мусор по таймеру из главного потока, с теми же порогами, что у Python.
"""

from __future__ import annotations

import gc

from PySide6.QtCore import QObject, QTimer

INTERVAL_MS = 500

_installed: GarbageCollector | None = None


class GarbageCollector(QObject):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.threshold = gc.get_threshold()
        gc.disable()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check)
        self.timer.start(INTERVAL_MS)

    def check(self) -> None:
        c0, c1, c2 = gc.get_count()
        if c0 > self.threshold[0]:
            gc.collect(0)
            if c1 > self.threshold[1]:
                gc.collect(1)
                if c2 > self.threshold[2]:
                    gc.collect(2)


def install(app: QObject) -> GarbageCollector:
    """Один раз на процесс; повторный вызов отдаёт уже установленный."""
    global _installed
    if _installed is None:
        _installed = GarbageCollector(app)
    return _installed
