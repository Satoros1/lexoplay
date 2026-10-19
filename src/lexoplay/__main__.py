"""Точка входа: `python -m lexoplay` или LexoPlay.exe.

--debug         сохранять кадры и OCR в %APPDATA%\\LexoPlay\\debug
--selftest      самопроверка (её гоняет CI на Windows), отчёт в --report
"""

from __future__ import annotations

import argparse
import sys


def _single_instance() -> bool:
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, "Local\\LexoPlaySingleInstance")
    _single_instance.handle = handle  # type: ignore[attr-defined]  # держим, пока жив процесс
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


def main() -> None:
    parser = argparse.ArgumentParser(prog="LexoPlay")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--report", default="selftest-report.json")
    args = parser.parse_args()

    if args.selftest:
        from lexoplay.selftest import run

        sys.exit(run(args.report))

    if sys.platform != "win32":
        print("LexoPlay работает только на Windows.")
        sys.exit(1)

    from PySide6.QtWidgets import QApplication, QMessageBox

    from lexoplay.config import Settings, data_dir
    from lexoplay.log import setup_logging

    settings = Settings.load()
    if args.debug:
        settings.debug = True
    setup_logging(settings.debug)

    qapp = QApplication(sys.argv)
    qapp.setApplicationName("LexoPlay")
    qapp.setQuitOnLastWindowClosed(False)

    if not _single_instance():
        QMessageBox.information(None, "LexoPlay", "LexoPlay уже запущен: его значок в трее рядом с часами.")
        sys.exit(0)

    from lexoplay.app import App
    from lexoplay.bg import BackgroundLoop
    from lexoplay.store import Store
    from lexoplay.ui.theme import app_icon

    qapp.setWindowIcon(app_icon())
    bg = BackgroundLoop()
    store = Store(data_dir() / "local.db")
    app = App(qapp, settings, store, bg)
    app.start()
    code = qapp.exec()
    app.shutdown()
    sys.exit(code)


if __name__ == "__main__":
    main()
