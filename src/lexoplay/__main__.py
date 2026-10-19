"""Точка входа: `python -m lexoplay` или LexoPlay.exe.

--debug         сохранять кадры и OCR в %APPDATA%\\LexoPlay\\debug
--selftest      самопроверка (её гоняет CI на Windows), отчёт в --report

Второй запуск не плодит копию и не ругается: он просит уже работающий LexoPlay
показать своё окно и сразу выходит.
"""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys

INSTANCE = f"LexoPlay-{getpass.getuser()}"


def _already_running() -> bool:
    """Достучаться до работающего LexoPlay; если он есть, он покажет окно."""
    from PySide6.QtNetwork import QLocalSocket

    sock = QLocalSocket()
    sock.connectToServer(INSTANCE)
    if not sock.waitForConnected(400):
        return False
    sock.write(b"show\n")
    sock.flush()
    sock.waitForBytesWritten(400)
    sock.disconnectFromServer()
    return True


def _listen(on_show):  # type: ignore[no-untyped-def]
    from PySide6.QtNetwork import QLocalServer

    QLocalServer.removeServer(INSTANCE)  # хвост от упавшего прошлого запуска
    server = QLocalServer()
    if not server.listen(INSTANCE):
        logging.getLogger(__name__).warning("single-instance сервер не поднялся: %s", server.errorString())

    def accept() -> None:
        while server.hasPendingConnections():
            conn = server.nextPendingConnection()
            conn.readyRead.connect(lambda c=conn: (c.readAll(), on_show()))
            conn.disconnected.connect(conn.deleteLater)

    server.newConnection.connect(accept)
    return server


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

    from PySide6.QtWidgets import QApplication

    qapp = QApplication(sys.argv)
    qapp.setApplicationName("LexoPlay")
    qapp.setQuitOnLastWindowClosed(False)
    if _already_running():
        sys.exit(0)

    from lexoplay.config import Settings, data_dir
    from lexoplay.log import setup_logging

    settings = Settings.load()
    if args.debug:
        settings.debug = True
    setup_logging(settings.debug)
    settings.save()  # новые поля и перенесённые клавиши сразу на диск

    from lexoplay.app import App
    from lexoplay.bg import BackgroundLoop
    from lexoplay.store import Store
    from lexoplay.ui.theme import app_icon, load_fonts

    load_fonts()
    qapp.setWindowIcon(app_icon())
    bg = BackgroundLoop()
    store = Store(data_dir() / "local.db")
    app = App(qapp, settings, store, bg)
    server = _listen(lambda: app.show_window())
    app.start()
    code = qapp.exec()
    server.close()
    try:
        app.shutdown()
    finally:
        logging.shutdown()
        # winrt и фоновые потоки иногда держат процесс: выходим наверняка, чтобы
        # следующий запуск не упёрся в «занятые» клавиши старого процесса
        os._exit(code)


if __name__ == "__main__":
    main()
