"""Сборка всего вместе: окно LexoPlay, трей, клавиши, события окон, оверлей, F2 и F3.

Главный поток = Qt. Захват, OCR и сеть идут в BackgroundLoop, результаты возвращаются
в поток Qt через Bridge. Состояния: idle -> busy -> idle (строка видна) / picking.
Режим слов (F3) живёт в своём окне WordLayer поверх игры и фокус не берёт.

Главное правило: игра не теряет фокус из-за нас. Строка перевода и режим слов фокус не
берут. Выделению области фокус нужен, поэтому оно рисуется поверх стоп-кадра игры, а
после Enter или Esc игра разворачивается и получает фокус обратно.

С привязкой к Lexo перевод идёт через сервер, слова уходят в карточки через outbox,
сессии закрываются сами. Без привязки F2 работает своим ключом Anthropic, F3 недоступен.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import ctypes
import logging
import os
import socket
import time
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from PIL import Image
from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QFont, QGuiApplication, QImage
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon, QWidget

from lexoplay import __version__
from lexoplay.bg import BackgroundLoop
from lexoplay.config import (
    Settings,
    data_dir,
    debug_dir,
    get_api_key,
    get_token,
    server_url,
    set_api_key,
    set_token,
)
from lexoplay.debugdump import DebugDump
from lexoplay.dedupe import LineMemory, LineRecord
from lexoplay.games import NOT_GAMES, WindowInfo, clean_title, is_game_exe
from lexoplay.geometry import Rect
from lexoplay.hotkeys import HotkeyError, parse
from lexoplay.lexo import Account, Deck, GameRef, LexoClient, LexoError, LexoOffline, Sense, Usage
from lexoplay.ocr_model import OcrResult
from lexoplay.outbox import Outbox
from lexoplay.pipeline import CaptureError, F2Outcome, Pipeline, ScanOutcome, Target
from lexoplay.preprocess import is_black, thumbnail
from lexoplay.session import GameSession, Sessions
from lexoplay.store import Game, Store, Task
from lexoplay.textproc import Utterance, to_utterance
from lexoplay.translate import AnthropicTranslator, LexoTranslator, Translator
from lexoplay.ui.main_window import AccountStatus, MainWindow
from lexoplay.ui.overlay import Overlay
from lexoplay.ui.region_picker import RegionPicker
from lexoplay.ui.theme import app_icon
from lexoplay.ui.word_layer import WordLayer
from lexoplay.vocab import Vocab
from lexoplay.watch import LineWatcher
from lexoplay.win import capture, winapi
from lexoplay.win.hooks import HotkeyManager, WindowEvents
from lexoplay.win.ocr import INSTALL_HINT, OcrUnavailable, WinOcr
from lexoplay.wordmode import CardState, WordView, build_views, selected_text

log = logging.getLogger(__name__)

RELEASES_URL = "https://github.com/Satoros1/lexoplay/releases/latest"
FRESH_OCR_S = 2.0  # F3 берёт распознанную для F2 реплику, если она моложе этого


def native_place(widget: QWidget, rect: Rect) -> None:
    winapi.place_topmost(int(widget.winId()), rect)


def overlay_native(hwnd: int) -> None:
    """Окна поверх игры: не попадают в наш же снимок экрана и не забирают фокус у игры."""
    winapi.exclude_from_capture(hwnd)
    winapi.set_no_activate(hwnd)


def pil_to_qimage(img: Image.Image) -> QImage:
    rgb = img.convert("RGB")
    data = rgb.tobytes("raw", "RGB")
    return QImage(data, rgb.width, rgb.height, rgb.width * 3, QImage.Format.Format_RGB888).copy()


@dataclass
class Active:
    hwnd: int
    pid: int
    exe: str


@dataclass
class CurrentLine:
    """Последняя распознанная реплика: с неё начинается режим слов."""

    exe: str
    ocr: OcrResult
    utterance: Utterance | None
    record: LineRecord | None
    at: float
    thumb: Image.Image | None = None

    @property
    def sentence(self) -> str:
        if self.record is not None:
            return self.record.source
        return self.utterance.text if self.utterance else ""


@dataclass
class WordCtx:
    exe: str
    title: str
    session_id: str
    line: CurrentLine
    views: list[WordView]
    selection: str = ""
    sense: Sense | None = None
    card: CardState | None = None
    seq: int = 0
    lookups: dict[str, Sense] = field(default_factory=dict)


class Bridge(QObject):
    """Сигналы из фонового потока в поток Qt."""

    f2_done = Signal(object, int)  # Future[F2Outcome], token
    watch_done = Signal(object, int)  # Future[bool], token
    ocr_checked = Signal(object)  # Future[str]
    call = Signal(object)  # функция без аргументов: выполнить в потоке Qt


class App(QObject):
    def __init__(self, qapp: QApplication, settings: Settings, store: Store, bg: BackgroundLoop) -> None:
        super().__init__()
        self.qapp = qapp
        self.settings = settings
        self.store = store
        self.bg = bg
        self.bridge = Bridge()
        self.bridge.f2_done.connect(self._on_f2_done)
        self.bridge.watch_done.connect(self._on_watch_done)
        self.bridge.ocr_checked.connect(self._on_ocr_checked)
        self.bridge.call.connect(lambda fn: fn())

        self.ocr = WinOcr("en-US")
        self.ocr_error: str | None = None
        self.translator: Translator | None = None
        self.pipelines: dict[str, Pipeline] = {}
        self.dump = DebugDump(debug_dir())

        # Lexo
        self.client = LexoClient(server_url(settings), get_token())
        self.account: Account | None = None
        self.usage: Usage | None = None
        self.lexo_offline = False
        self.update_required = False
        self.server_note = ""  # «на сервере ещё нет LexoPlay» и подобное
        self._auth_lost_at = 0.0
        self.vocab = Vocab(store)
        self.outbox = Outbox(
            store,
            self._send_task,
            on_change=lambda: self.bridge.call.emit(self._update_tray),
            on_paused=lambda exc: self.bridge.call.emit(lambda: self._on_lexo_error(exc)),
            on_sent=self._on_task_sent,
        )
        self.sessions = Sessions(on_end=self._session_ended)

        self.overlay = Overlay(placer=native_place, on_native=overlay_native)
        self.picker: RegionPicker | None = None
        self.word_layer = WordLayer(placer=native_place, on_native=overlay_native)
        self._apply_overlay_settings()
        self.word_layer.selected.connect(self._on_word_selected)
        self.word_layer.add_clicked.connect(self._on_word_add)
        self.word_layer.finished.connect(self._on_words_finished)
        self.word_ctx: WordCtx | None = None
        self.current: CurrentLine | None = None
        self.window: MainWindow | None = None

        self.active: Active | None = None
        self.display: Rect | None = None
        self.state = "idle"  # idle | busy | picking
        self.token = 0
        self.watcher: LineWatcher | None = None  # следит, не сменилась ли показанная реплика
        self._watch_pending = False
        self._hotkeys_paused = False
        self._failed_keys: set[str] = set()

        self.watch_timer = QTimer(self)
        self.watch_timer.setInterval(400)
        self.watch_timer.timeout.connect(self._watch_tick)
        self.geom_timer = QTimer(self)
        self.geom_timer.setInterval(700)
        self.geom_timer.timeout.connect(self._geom_tick)
        self.session_timer = QTimer(self)
        self.session_timer.setInterval(60_000)
        self.session_timer.timeout.connect(self.sessions.tick)
        self.vocab_timer = QTimer(self)
        self.vocab_timer.setInterval(10 * 60_000)
        self.vocab_timer.timeout.connect(lambda: self._refresh_vocab(full=False))

        self.hotkeys = HotkeyManager()
        self.hotkeys.triggered.connect(self._on_hotkey)
        self.hotkeys.failed.connect(self._on_hotkey_failed)
        self._define_hotkeys()

        self.events = WindowEvents()
        self.events.foreground.connect(self._on_foreground)
        # при перетаскивании окна событий десятки в секунду: пересчитываем не чаще раза в 60 мс
        self._move_timer = QTimer(self)
        self._move_timer.setSingleShot(True)
        self._move_timer.setInterval(60)
        self._move_timer.timeout.connect(self._sync_geometry)
        self.events.game_moved.connect(lambda _h: self._move_timer.start())
        self.events.game_minimized.connect(self._on_minimized)
        self.events.game_destroyed.connect(self._on_destroyed)

        self.tray = self._build_tray()
        self._purge_non_games()
        self._rebuild_translator()

    # ------------------------------------------------------------------ запуск и выход
    def start(self) -> None:
        log.info("LexoPlay %s, игр в списке: %s, Lexo: %s", __version__, len(self.store.list_games()), self.client.linked)
        self.hotkeys.set_group("global", True)
        self._on_foreground(winapi.foreground_window())
        self.bridge_ocr_check()
        self._outbox_worker = self.bg.submit(self.outbox.run_forever())
        self.session_timer.start()
        self.vocab_timer.start()
        self._update_tray()
        self.tray.show()
        if self.client.linked:
            self._check_account()
        first_run = not self.store.list_games() or (not self.client.linked and not get_api_key())
        if first_run:
            page = "lexo" if not self.client.linked and not get_api_key() else "games"
            QTimer.singleShot(200, lambda: self.show_window(page))
        else:
            hk = self.settings.hotkeys
            self.tray.showMessage(
                "LexoPlay запущен",
                f"В игре {hk.translate} переводит реплику, {hk.words} сохраняет слова. Окно LexoPlay открывается из трея.",
                app_icon(),
                5000,
            )

    def shutdown(self) -> None:
        self.word_layer.finish("closed")
        self.sessions.end_all()
        try:  # дослать конец сессий и свежие слова; что не успело, уйдёт при следующем запуске
            self.bg.run(asyncio.wait_for(self.outbox.flush(), 3), timeout=4)
        except Exception:
            log.info("outbox не успел досылать при выходе")
        worker = getattr(self, "_outbox_worker", None)
        if worker is not None:
            worker.cancel()
        try:
            self.bg.run(self.client.close(), timeout=2)
        except Exception:
            log.info("httpx-клиент не закрылся за 2 с")
        self.hotkeys.clear()
        self.events.close()
        self.tray.hide()
        self.overlay.close()
        self.word_layer.close()
        if self.window is not None:
            self.window.deleteLater()
        self.bg.stop()
        self.store.close()

    def run_bg(self, coro: Coroutine[Any, Any, Any], done: Callable[[Any, BaseException | None], None]) -> None:
        """Корутину в фоновый цикл, результат (или ошибку) обратно в поток Qt."""

        async def wrapped() -> tuple[Any, BaseException | None]:
            try:
                return await coro, None
            except Exception as exc:
                return None, exc

        def back(f: concurrent.futures.Future) -> None:
            if f.cancelled():
                return
            result, err = f.result()
            self.bridge.call.emit(lambda: done(result, err))

        self.bg.submit(wrapped()).add_done_callback(back)

    def _purge_non_games(self) -> None:
        """0.1–0.2 разрешали сделать игрой любое окно: Chrome, Steam. Такие убираем."""
        for g in self.store.list_games():
            if g.exe in NOT_GAMES:
                log.info("убираю из игр %s (%s): это не игра", g.title, g.exe)
                self.store.delete_game(g.exe)

    # ------------------------------------------------------------------ OCR
    def bridge_ocr_check(self) -> None:
        async def check() -> str:
            return self.ocr.check()

        fut = self.bg.submit(check())
        fut.add_done_callback(lambda f: self.bridge.ocr_checked.emit(f))

    def _on_ocr_checked(self, fut: concurrent.futures.Future) -> None:
        try:
            lang = fut.result()
            self.ocr_error = None
            log.info("OCR готов: %s", lang)
        except OcrUnavailable as exc:
            self.ocr_error = str(exc)
            self._ask_install_ocr()
        except Exception as exc:  # winrt не загрузился вообще
            log.exception("OCR не запустился")
            self.ocr_error = str(exc)
            QMessageBox.critical(None, "LexoPlay", f"Windows OCR не запустился: {exc}")

    def _ask_install_ocr(self) -> None:
        box = QMessageBox()
        box.setWindowIcon(app_icon())
        box.setWindowTitle("LexoPlay: нужен английский OCR")
        box.setText(
            "В Windows не установлен пакет распознавания английского текста. Без него LexoPlay не прочитает реплики.\n\n"
            "Нажми «Установить»: откроется PowerShell от имени администратора, установка займёт около минуты. "
            "Потом перезапусти LexoPlay."
        )
        install = box.addButton("Установить", QMessageBox.ButtonRole.AcceptRole)
        copy = box.addButton("Скопировать команду", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Позже", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is install:
            params = f'-NoProfile -Command "{INSTALL_HINT}; Read-Host \'Готово. Нажми Enter\'"'
            ctypes.windll.shell32.ShellExecuteW(None, "runas", "powershell.exe", params, None, 1)
        elif box.clickedButton() is copy:
            QGuiApplication.clipboard().setText(INSTALL_HINT)

    # ------------------------------------------------------------------ окно LexoPlay (контроллер)
    def show_window(self, page: str | None = None) -> None:
        if self.window is None:
            self.window = MainWindow(self)
        self._close_words("closed")
        self.window.open(page)
        winapi.force_foreground(int(self.window.winId()))

    def window_hidden(self) -> None:
        if not self.store.get_meta("tray_hint_shown"):
            self.store.set_meta("tray_hint_shown", "1")
            self.tray.showMessage(
                "LexoPlay работает в трее",
                "Перевод в играх продолжает работать. Окно открывается по значку LexoPlay у часов, выход через его меню.",
                app_icon(),
                5000,
            )

    def _refresh_window(self) -> None:
        if self.window is not None:
            self.window.refresh_account()
            self.window.refresh_games()

    def account_status(self) -> AccountStatus:
        pending, failed = self.outbox.counts()
        note = self.server_note
        if not self.client.linked:
            return AccountStatus(False, queue=pending, failed=failed, note=note)
        return AccountStatus(
            True,
            self.account.label if self.account else "аккаунт привязан",
            premium=bool(self.account and self.account.premium),
            used=self.usage.used if self.usage else None,
            limit=self.usage.limit if self.usage else None,
            offline=self.lexo_offline,
            queue=pending,
            failed=failed,
            note=note,
        )

    def active_exe(self) -> str | None:
        return self.active.exe if self.active else None

    def api_key(self) -> str:
        return get_api_key() or ""

    def set_api_key(self, key: str) -> None:
        if key == self.api_key():
            return
        set_api_key(key)
        self._rebuild_translator()
        self._refresh_window()

    def retry_failed(self) -> None:
        self.outbox.retry_failed()

    def open_logs(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(data_dir())))

    def make_report(self) -> str | None:
        from lexoplay.report import make_report, reveal

        try:
            path = make_report(self.settings, self.store, extra=self._report_extra())
        except Exception:
            log.exception("отчёт не собрался")
            return None
        reveal(path)
        return str(path)

    def _report_extra(self) -> dict[str, Any]:
        pending, failed = self.outbox.counts()
        return {
            "linked": self.client.linked,
            "server": self.client.server,
            "account": self.account.label if self.account else None,
            "usage": [self.usage.used, self.usage.limit] if self.usage else None,
            "offline": self.lexo_offline,
            "server_note": self.server_note,
            "ocr_error": self.ocr_error,
            "outbox": {"pending": pending, "failed": failed},
            "active": self.active.exe if self.active else None,
            "display": self.display.to_dict() if self.display else None,
            "state": self.state,
            "failed_hotkeys": sorted(k for k in self._failed_keys if not k.startswith("warned:")),
            "translator": type(self.translator).__name__ if self.translator else None,
        }

    def settings_changed(self) -> None:
        self.settings.save()
        self._apply_overlay_settings()
        new_server = server_url(self.settings)
        if new_server != self.client.server:
            old = self.client
            self.client = LexoClient(new_server, old.token)
            self.bg.submit(old.close())
            self.server_note = ""
            if self.client.linked:
                self._check_account()
        self._rebuild_translator()
        self._define_hotkeys()
        self._apply_hotkey_groups()
        self._update_tray()

    def pause_hotkeys(self, paused: bool) -> None:
        """Пока игрок вводит новую клавишу в окне LexoPlay, наши клавиши её не перехватывают."""
        self._hotkeys_paused = paused
        self._apply_hotkey_groups()

    def list_windows(self) -> list[WindowInfo]:
        out = []
        for hwnd, title, exe, path, rect, minimized in winapi.app_windows():
            out.append(WindowInfo(hwnd, title, exe, path, int(rect.w), int(rect.h), minimized))
        return out

    def add_game(self, w: WindowInfo) -> Game:
        existing = self.store.get_game(w.exe)
        game = existing or Game(w.exe, clean_title(w.title, w.exe), deck_mode=self.settings.deck)
        game = self.store.save_game(game)
        log.info("игра добавлена: %s (%s)", game.title, game.exe)
        self._update_tray()
        if game.region is None:
            QTimer.singleShot(250, lambda: self.pick_region(game.exe))
        return game

    def save_game(self, game: Game) -> None:
        self.store.save_game(game)
        self._set_status()

    def forget_game(self, exe: str) -> None:
        self.store.delete_game(exe)
        self.pipelines.pop(exe, None)
        if self.active and self.active.exe == exe:
            self._hide_strip()
            self._close_words("closed")
            self.events.unwatch_game()
            self.hotkeys.set_group("game", False)
            self.active = None
        self._set_status()

    def pick_region(self, exe: str) -> None:
        """«Выделить окно диалога» из окна LexoPlay: развернуть игру и открыть выделение поверх неё."""
        hwnd = winapi.find_game_window(exe)
        if hwnd is None:
            if self.window is not None:
                self.window.show_message(
                    "Игра сейчас не запущена. Запусти её, а потом нажми здесь «Выделить окно диалога» "
                    f"или прямо в игре {self.settings.hotkeys.translate}."
                )
            return
        if self.window is not None:
            self.window.hide()
        winapi.restore_window(hwnd)
        winapi.force_foreground(hwnd)
        QTimer.singleShot(650, lambda: self._pick_when_ready(exe, hwnd, 0))

    def _pick_when_ready(self, exe: str, hwnd: int, tries: int) -> None:
        if not (self.active and self.active.hwnd == hwnd):
            if tries < 3:
                winapi.force_foreground(hwnd)
                QTimer.singleShot(400, lambda: self._pick_when_ready(exe, hwnd, tries + 1))
                return
            # фокус так и не пришёл (игра от администратора?): всё равно выделяем по её окну
            pid = winapi.window_pid(hwnd)
            self.active = Active(hwnd, pid, exe)
            self.events.watch_game(hwnd, pid)
        self.open_picker()

    # ------------------------------------------------------------------ настройки
    def _define_hotkeys(self) -> None:
        self.hotkeys.clear()
        hk = self.settings.hotkeys
        for name, text, group in (
            ("translate", hk.translate, "game"),
            ("words", hk.words, "game"),
            ("hide", hk.hide, "game"),
            ("reselect", hk.reselect, "game"),
            ("add_game", hk.add_game, "global"),
            ("words_esc", "Escape", "words"),
            ("words_enter", "Enter", "words"),
        ):
            if not text:
                continue
            try:
                self.hotkeys.define(name, parse(text), group)
            except HotkeyError as exc:
                log.warning("хоткей %s=%s: %s", name, text, exc)

    def _apply_hotkey_groups(self) -> None:
        paused = self._hotkeys_paused
        self.hotkeys.set_group("global", not paused)
        self.hotkeys.set_group("game", not paused and self._game_in_focus())
        self.hotkeys.set_group("words", not paused and self.word_layer.is_open)

    def _apply_overlay_settings(self) -> None:
        self.overlay.font_scale = self.settings.font_scale
        self.overlay.opacity = self.settings.strip_opacity
        self.word_layer.font_scale = self.settings.font_scale

    def _rebuild_translator(self) -> None:
        if self.client.linked:
            self.translator = LexoTranslator(
                self.client, on_usage=lambda u: self.bridge.call.emit(lambda: self._set_usage(u))
            )
        else:
            key = get_api_key()
            self.translator = AnthropicTranslator(key, self.settings.model) if key else None
        for p in self.pipelines.values():
            if self.translator:
                p.translator = self.translator
            p.target_lang = self.settings.target_lang
            p.source_lang = self.settings.source_lang

    # ------------------------------------------------------------------ аккаунт Lexo
    def _device_name(self) -> str:
        name = os.environ.get("COMPUTERNAME") or socket.gethostname() or "ПК"
        return name[:64]

    def link(self, code: str, done: Callable[[str | None], None]) -> None:
        def finished(result: Any, err: BaseException | None) -> None:
            if err is not None:
                self.client.token = None
                if isinstance(err, LexoError):
                    if err.code == "server_outdated":
                        self.server_note = err.message
                    done(err.message)
                elif isinstance(err, LexoOffline):
                    done("Нет связи с Lexo. Проверь интернет и адрес сервера во вкладке «Ещё».")
                else:
                    log.error("привязка упала", exc_info=err)
                    done(f"Не получилось привязать: {err}")
                return
            token, account = result
            if not set_token(token):
                log.warning("токен не сохранился в Credential Manager: после перезапуска привязку придётся повторить")
            self.account = account
            self.lexo_offline = False
            self.update_required = False
            self.server_note = ""
            self.vocab.clear()
            self.outbox.resume()
            self._rebuild_translator()
            self._check_account()
            self._update_tray()
            done(None)

        self.run_bg(self.client.link(code, self._device_name()), finished)

    def unlink(self, done: Callable[[], None]) -> None:
        def finished(_r: Any, err: BaseException | None) -> None:
            if err is not None:
                log.info("отвязка на сервере не прошла (%s), забываем токен локально", err)
            self._forget_token()
            done()

        self.run_bg(self.client.unlink(), finished)

    def load_decks(self, done: Callable[[list[Deck], int | None, str | None], None]) -> None:
        def finished(result: Any, err: BaseException | None) -> None:
            if err is None:
                decks, current = result
                done(decks, current, None)
                return
            if isinstance(err, LexoError) and err.status in (401, 426):
                self._on_lexo_error(err)
            done([], None, self._error_text(err))

        self.run_bg(self.client.decks(), finished)

    def create_deck(self, title: str, done: Callable[[Deck | None, str | None], None]) -> None:
        def finished(deck: Any, err: BaseException | None) -> None:
            done(deck, None if err is None else self._error_text(err))

        self.run_bg(self.client.create_deck(title), finished)

    @staticmethod
    def _error_text(err: BaseException) -> str:
        if isinstance(err, LexoError):
            return err.message
        if isinstance(err, LexoOffline):
            return "Нет связи с Lexo."
        return f"Ошибка: {err}"

    def _forget_token(self) -> None:
        set_token(None)
        self.client.token = None
        self.account = None
        self.usage = None
        self.vocab.clear()
        self._rebuild_translator()
        self._update_tray()
        self._refresh_window()

    def _check_account(self) -> None:
        def finished(result: Any, err: BaseException | None) -> None:
            if err is None:
                self.account, usage, _device = result
                if usage:
                    self.usage = usage
                self.lexo_offline = False
                self.server_note = ""
                self._refresh_vocab(full=True)
            elif isinstance(err, LexoOffline):
                self.lexo_offline = True
            elif isinstance(err, LexoError):
                self._on_lexo_error(err)
            self._update_tray()
            self._refresh_window()

        self.run_bg(self.client.me(), finished)

    def _refresh_vocab(self, *, full: bool) -> None:
        if not self.client.linked:
            return
        since = None if full or not self.vocab.since else self.vocab.since

        def finished(result: Any, err: BaseException | None) -> None:
            if err is not None:
                if isinstance(err, LexoError):
                    self._on_lexo_error(err)
                return
            items, server_time = result
            if since is None:
                self.vocab.replace(items, server_time)
            else:
                self.vocab.merge(items, server_time)
            log.info("словарь Lexo: %s слов", len(self.vocab))

        self.run_bg(self.client.vocab(since), finished)

    def _set_usage(self, usage: Usage) -> None:
        self.usage = usage
        self.lexo_offline = False
        self.outbox.nudge()
        self._update_tray()
        if self.window is not None and self.window.isVisible():
            self.window.refresh_account()

    def _on_lexo_error(self, err: LexoError) -> None:
        if err.status == 401:
            self._auth_lost(err.message)
        elif err.status == 426:
            if not self.update_required:
                self.update_required = True
                self.tray.showMessage("LexoPlay", err.message, QSystemTrayIcon.MessageIcon.Warning, 8000)
            self.overlay.toast(err.message, 4000)
            self._update_tray()
        elif err.code == "server_outdated":
            self.server_note = err.message
            self._refresh_window()

    def _auth_lost(self, message: str) -> None:
        """Привязку сняли в боте или в Mini App: токен больше не годится."""
        self._close_words("closed")
        self._forget_token()
        if time.time() - self._auth_lost_at < 30:
            return
        self._auth_lost_at = time.time()
        self.overlay.toast("Привязка снята, привяжи ПК заново", 3500)
        self.tray.showMessage("LexoPlay", f"{message} Код возьми в боте командой /link.", app_icon(), 8000)

    # ------------------------------------------------------------------ outbox и сессии
    async def _send_task(self, task: Task) -> Any:
        if not self.client.linked:
            raise LexoError(401, "unauthorized", "ПК не привязан к Lexo.")
        if task.kind == "card":
            return await self.client.add_card(task.payload)
        if task.kind == "end":
            payload = dict(task.payload)
            session_id = payload.pop("session_id")
            return await self.client.end_session(session_id, payload)
        log.warning("outbox: неизвестная задача %s, выбрасываю", task.kind)
        return None

    def _on_task_sent(self, task: Task, result: Any) -> None:
        # фоновый поток: Vocab сам под замком
        if task.kind == "card" and isinstance(result, dict) and result.get("text"):
            self.vocab.add(str(result["text"]))

    def _session_ended(self, s: GameSession, duration: int, ended_at: float) -> None:
        log.info("сессия %s (%s): %s с, %s реплик, %s слов", s.id, s.exe, duration, s.lines, s.words)
        if not self.client.linked or (s.lines == 0 and s.words == 0):
            return
        self.outbox.enqueue(
            "end",
            {
                "session_id": s.id,
                "duration_s": min(duration, 7 * 86400),
                "lines_count": s.lines,
                "ended_at": datetime.fromtimestamp(ended_at, UTC).isoformat(),
            },
        )

    # ------------------------------------------------------------------ трей
    def _tooltip(self) -> str:
        if self.active:
            g = self.store.get_game(self.active.exe)
            text = f"LexoPlay: {g.title if g else self.active.exe}"
        else:
            text = "LexoPlay: игра не открыта"
        if self.client.linked and self.usage:
            text += f"\nПереводов сегодня: {self.usage.used} из {self.usage.limit}"
        return text[:127]

    def _build_tray(self) -> QSystemTrayIcon:
        tray = QSystemTrayIcon(app_icon(), self)
        menu = QMenu()
        open_action = QAction("Открыть LexoPlay", menu)
        f = QFont(open_action.font())
        f.setBold(True)
        open_action.setFont(f)
        open_action.triggered.connect(lambda: self.show_window())
        menu.addAction(open_action)
        menu.addSeparator()
        self._status_action = QAction("Игра не открыта", menu)
        self._status_action.setEnabled(False)
        menu.addAction(self._status_action)
        self._lexo_action = QAction("Привязать ПК к Lexo…", menu)
        self._lexo_action.triggered.connect(lambda: self.show_window("lexo"))
        menu.addAction(self._lexo_action)
        self._queue_action = QAction("", menu)
        self._queue_action.setEnabled(False)
        self._queue_action.setVisible(False)
        menu.addAction(self._queue_action)
        self._failed_action = QAction("", menu)
        self._failed_action.triggered.connect(self.outbox.retry_failed)
        self._failed_action.setVisible(False)
        menu.addAction(self._failed_action)
        self._update_action = QAction("Скачать новую версию", menu)
        self._update_action.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(RELEASES_URL)))
        self._update_action.setVisible(False)
        menu.addAction(self._update_action)
        menu.addSeparator()
        menu.addAction("Выход", self.qapp.quit)
        tray.setContextMenu(menu)
        tray.setToolTip(self._tooltip())
        tray.activated.connect(self._on_tray)
        self._menu = menu
        return tray

    def _on_tray(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_window()

    def _update_tray(self) -> None:
        if self.client.linked:
            who = self.account.label if self.account else "привязан"
            state = ", нет связи" if self.lexo_offline else ""
            self._lexo_action.setText(f"Lexo: {who}{state}")
        else:
            self._lexo_action.setText("Привязать ПК к Lexo…")
        pending, failed = self.outbox.counts()
        self._queue_action.setText(f"В очереди на отправку: {pending}")
        self._queue_action.setVisible(pending > 0)
        self._failed_action.setText(f"Не отправлено: {failed}. Повторить")
        self._failed_action.setVisible(failed > 0)
        self._update_action.setVisible(self.update_required)
        self.tray.setToolTip(self._tooltip())

    def _set_status(self) -> None:
        g = self.store.get_game(self.active.exe) if self.active else None
        self._status_action.setText(f"Игра: {g.title}" if g else "Игра не открыта")
        self._update_tray()
        if self.window is not None:
            self.window.refresh_games()

    # ------------------------------------------------------------------ окна
    def _own_window(self, root: int) -> bool:
        return self.window is not None and root == int(self.window.winId())

    def _on_foreground(self, hwnd: int) -> None:
        if not hwnd:
            return
        root = winapi.root_window(hwnd)
        pid = winapi.window_pid(root)
        if pid == os.getpid():
            # наше окно: выделение области (не трогаем) или окно LexoPlay (клавиши игры не нужны)
            if self._own_window(root):
                self._close_words("focus")
                self.hotkeys.set_group("game", False)
            return
        exe = winapi.process_exe(pid)
        game = self.store.get_game(exe) if exe and is_game_exe(exe) else None
        if game is not None:
            if not self.active or self.active.hwnd != root:
                self._hide_strip()
                self._close_words("switch")
            self.active = Active(root, pid, game.exe)
            self.events.watch_game(root, pid)
            self.hotkeys.set_group("game", not self._hotkeys_paused)
            self.store.touch(game.exe)
            rect = self._sync_geometry()
            log.info("игра в фокусе: %s, видна в %s", game.exe, rect.to_dict() if rect else None)
            self._set_status()
        else:
            self.hotkeys.set_group("game", False)
            self._hide_strip()
            self._close_words("focus")

    def _on_minimized(self, _hwnd: int, minimized: bool) -> None:
        if minimized and self.state != "picking":
            self._close_words("closed")
            self._hide_strip()

    def _on_destroyed(self, _hwnd: int) -> None:
        self._close_words("closed")
        self._hide_strip()
        if self.active:
            self.sessions.game_closed(self.active.exe)
        self.current = None
        self.events.unwatch_game()
        self.hotkeys.set_group("game", False)
        self.active = None
        self._set_status()

    def _sync_geometry(self) -> Rect | None:
        if not self.active or not winapi.is_window(self.active.hwnd):
            return None
        rect, _scaled = winapi.display_rect(self.active.hwnd, self.settings.magpie_mode)
        if rect.empty:
            return None
        self.display = rect
        self.overlay.set_display_rect(rect)
        self.word_layer.set_display_rect(rect)
        return rect

    def _geom_tick(self) -> None:
        if self.overlay.isVisible():
            self._sync_geometry()
        else:
            self.geom_timer.stop()

    def _game_in_focus(self) -> bool:
        return bool(self.active) and winapi.root_window(winapi.foreground_window()) == self.active.hwnd

    def _return_to_game(self) -> None:
        if self.active and winapi.is_window(self.active.hwnd):
            winapi.restore_window(self.active.hwnd)
            winapi.force_foreground(self.active.hwnd)

    # ------------------------------------------------------------------ клавиши
    def _on_hotkey_failed(self, name: str, text: str) -> None:
        self._failed_keys.add(f"{name}={text}")
        key = f"warned:{text}"
        if key in self._failed_keys or name.startswith("words_"):
            return
        self._failed_keys.add(key)
        self.tray.showMessage(
            "LexoPlay",
            f"Клавишу {text} уже занимает другая программа (или второй LexoPlay). Выбери другую: окно LexoPlay → Клавиши.",
            QSystemTrayIcon.MessageIcon.Warning,
            6000,
        )

    def _on_hotkey(self, name: str) -> None:
        if self.state == "picking":
            return
        if name == "words_esc":
            self._close_words("esc")
            return
        if name == "words_enter":
            if self.word_layer.is_open:
                self.word_layer.press_enter()
            return
        if self.word_layer.is_open and name in ("translate", "words", "hide"):
            self._close_words("hotkey")
            return
        if name == "add_game":
            self.add_current_game()
            return
        if not self._game_in_focus():
            return
        if name == "translate":
            self.f2()
        elif name == "hide":
            self._hide_strip()
        elif name == "reselect":
            self.open_picker()
        elif name == "words":
            self.f3()

    def add_current_game(self) -> None:
        hwnd = winapi.root_window(winapi.foreground_window())
        pid = winapi.window_pid(hwnd)
        if not hwnd or pid == os.getpid():
            return
        exe = winapi.process_exe(pid)
        if not exe:
            self.tray.showMessage("LexoPlay", "Не получилось определить игру. Попробуй запустить LexoPlay от имени администратора.")
            return
        if not is_game_exe(exe):
            self.overlay.set_display_rect(winapi.client_rect_screen(hwnd))
            self.overlay.toast(f"{exe} не похоже на игру. Добавь игру из окна LexoPlay.", 3500)
            return
        game = self.store.get_game(exe) or self.store.save_game(
            Game(exe, clean_title(winapi.window_title(hwnd), exe), deck_mode=self.settings.deck)
        )
        self.active = Active(hwnd, pid, game.exe)
        self.events.watch_game(hwnd, pid)
        self.hotkeys.set_group("game", True)
        self._set_status()
        self.open_picker()

    # ------------------------------------------------------------------ выделение области
    def open_picker(self) -> None:
        """Снять стоп-кадр игры и открыть поверх него выделение окна диалога."""
        if not self.active or self.state == "picking":
            return
        display = self._sync_geometry()
        game = self.store.get_game(self.active.exe)
        if display is None or game is None:
            return
        self._hide_strip()
        self._close_words("closed")
        self.state = "picking"
        grab = self._make_grab(self.active, game, display)

        def shot(img: Any, err: BaseException | None) -> None:
            if self.state != "picking" or self.picker is not None:
                return
            if err is not None:
                log.info("стоп-кадр для выделения не снялся: %s", err)
            self._show_picker(game, display, pil_to_qimage(img) if img is not None else None)

        self.run_bg(asyncio.to_thread(grab), shot)

    def _show_picker(self, game: Game, display: Rect, background: QImage | None) -> None:
        picker = RegionPicker(
            display,
            game.title,
            game.region,
            game.name_region,
            placer=native_place,
            on_native=winapi.exclude_from_capture,
            background=background,
        )
        picker.accepted.connect(self._on_picked)
        picker.cancelled.connect(self._on_pick_cancelled)
        self.picker = picker
        picker.open()
        winapi.force_foreground(int(picker.winId()))

    def _on_picked(self, region: Rect, name_region: Rect | None) -> None:
        self.picker = None
        self.state = "idle"
        if not self.active:
            return
        game = self.store.get_game(self.active.exe)
        if game:
            self.store.save_game(game.with_(region=region, name_region=name_region))
            log.info("область диалога %s: %s", game.exe, region.to_dict())
        self._return_to_game()
        self._set_status()
        QTimer.singleShot(300, lambda: self._f2_when_back(0))  # сразу показать перевод текущей реплики

    def _f2_when_back(self, tries: int) -> None:
        """Полноэкранная игра разворачивается не мгновенно: переводим, когда она снова на экране."""
        if not self.active:
            return
        back = self._game_in_focus() and not winapi.is_minimized(self.active.hwnd)
        if not back and tries < 12:
            if tries % 4 == 3:
                self._return_to_game()
            QTimer.singleShot(250, lambda: self._f2_when_back(tries + 1))
            return
        QTimer.singleShot(250, self.f2)  # ещё кадр-другой на перерисовку после разворота

    def _on_pick_cancelled(self) -> None:
        self.picker = None
        self.state = "idle"
        self._return_to_game()

    # ------------------------------------------------------------------ F2
    def _pipeline(self, exe: str) -> Pipeline | None:
        if self.translator is None:
            return None
        p = self.pipelines.get(exe)
        if p is None:
            p = Pipeline(
                self.ocr,
                self.translator,
                LineMemory(),
                target_lang=self.settings.target_lang,
                source_lang=self.settings.source_lang,
                context_size=self.settings.context_lines,
                debug=self.dump if self.settings.debug else None,
            )
            self.pipelines[exe] = p
        p._debug = self.dump if self.settings.debug else None  # noqa: SLF001
        return p

    def _make_grab(self, active: Active, game: Game, display: Rect):  # type: ignore[no-untyped-def]
        hwnd, store, exe = active.hwnd, self.store, game.exe
        mode = {"value": game.capture}

        def remember(value: str) -> None:
            if mode["value"] != value:
                mode["value"] = value
                g = store.get_game(exe)
                if g:
                    store.save_game(g.with_(capture=value))
                log.info("%s: способ захвата %s", exe, value)

        def grab():  # type: ignore[no-untyped-def]
            # Основной способ: снимок экрана там, где игра видна. Он совпадает с тем, что
            # видит игрок (Magpie, DPI-масштабирование старых игр), а наши окна из него
            # исключены. PrintWindow запасной: для окон, которые в снимке экрана чёрные.
            if not winapi.is_window(hwnd):
                raise CaptureError("окно игры закрыто")
            if winapi.is_minimized(hwnd):
                raise CaptureError("окно игры свёрнуто")
            if mode["value"] != "window":
                img = capture.grab_screen(display)
                if not is_black(img):
                    remember("screen")
                    return img
            img = capture.grab_window(hwnd)
            if is_black(img):
                raise CaptureError("кадр пустой")
            remember("window")
            return img

        return grab

    def f2(self) -> None:
        if self.state != "idle" or not self.active:
            return
        if self.overlay.strip_visible:
            self._hide_strip()
            return
        game = self.store.get_game(self.active.exe)
        if game is None:
            return
        if game.region is None:
            self.open_picker()
            return
        if self.ocr_error:
            self.overlay.toast("Нет английского OCR в Windows. Подробности в трее.")
            self._ask_install_ocr()
            return
        pipeline = self._pipeline(game.exe)
        if pipeline is None:
            self.overlay.toast("Перевод не настроен: открой LexoPlay из трея и привяжи Lexo", 3500)
            return
        display = self._sync_geometry()
        if display is None:
            return
        session = self.sessions.touch(game.exe, game.title)
        self.state = "busy"
        self.token += 1
        token = self.token
        self.watch_timer.stop()
        self.overlay.show_busy(game.region)
        self.geom_timer.start()
        if self.settings.debug:
            self.dump.new_shot()
        target = Target(game.title, game.region, game.name_region, game.binarize, game.exe, session.id)
        region = game.region

        def partial(text: str, t: int = token) -> None:
            # из фонового потока: перевод дописывается, показываем как есть
            self.bridge.call.emit(lambda: self._show_partial(t, region, text))

        fut = self.bg.submit(pipeline.run(self._make_grab(self.active, game, display), target, on_partial=partial))
        fut.add_done_callback(lambda f, t=token: self.bridge.f2_done.emit(f, t))

    def _show_partial(self, token: int, region: Rect, text: str) -> None:
        if token == self.token and self.state == "busy" and text:
            self.overlay.show_text(region, text)

    def _on_f2_done(self, fut: concurrent.futures.Future, token: int) -> None:
        if token != self.token:
            return
        self.state = "idle"
        try:
            out: F2Outcome = fut.result()
        except Exception as exc:
            log.exception("F2 упал")
            self._hide_strip()
            self.overlay.toast(f"Ошибка: {exc}")
            return
        log.info("F2 %s %s %s", out.kind, out.code or "", {k: round(v, 3) for k, v in out.timings.items()})
        if not self._game_in_focus() or not self.active:
            self._hide_strip()
            return
        game = self.store.get_game(self.active.exe)
        if out.kind in ("translated", "repeat") and out.record and game and game.region:
            if out.kind == "translated":
                self.sessions.line_translated(game.exe)
            if out.ocr is not None:
                self.current = CurrentLine(game.exe, out.ocr, out.utterance, out.record, time.time(), out.thumb)
            self.overlay.show_text(game.region, out.record.translation)
            self._watch_line(out.utterance.text if out.utterance else out.record.source, out.thumb)
        elif out.kind == "no_text":
            self._hide_strip()
            self.overlay.toast(f"Текст не найден. {self.settings.hotkeys.reselect}, чтобы выделить окно диалога заново")
        elif out.code == "limit_reached" and game and game.region:
            # строка на месте перевода: видно, почему перевода нет; F3 при этом работает
            if out.ocr is not None:
                self.current = CurrentLine(game.exe, out.ocr, out.utterance, None, time.time(), out.thumb)
            self.overlay.show_text(game.region, out.message or "Лимит переводов на сегодня закончился.")
            self._watch_line(out.utterance.text if out.utterance else "", out.thumb)
        elif out.code in ("device_revoked", "unauthorized"):
            self._hide_strip()
            self._auth_lost(out.message or "Привязка снята.")
        elif out.code == "update_required":
            self._hide_strip()
            self._on_lexo_error(LexoError(426, "update_required", out.message or "Вышла новая версия LexoPlay."))
        else:
            self._hide_strip()
            if out.code == "offline":
                self.lexo_offline = True
                self._update_tray()
            elif out.code == "server_outdated":
                self.server_note = out.message or ""
            self.overlay.toast(out.message or "Не получилось перевести", 4000)

    def _hide_strip(self) -> None:
        if self.state == "busy":
            self.token += 1  # результат пришедшего позже запроса не показываем
            self.state = "idle"
        self.overlay.hide_strip()
        if not self.word_layer.is_open:
            self.watch_timer.stop()
            self.watcher = None

    # ------------------------------------------------------------------ смена реплики
    def _watch_line(self, text: str, thumb: Image.Image | None) -> None:
        """Следить за показанной репликой: перевод уберём, когда она сменится."""
        self.watcher = LineWatcher(text, thumb) if thumb is not None else None
        if self.watcher is not None:
            self.watch_timer.start()

    def _watch_tick(self) -> None:
        watching = self.overlay.strip_visible or self.word_layer.is_open
        watcher = self.watcher
        if not watching or watcher is None or self.state != "idle" or self._watch_pending or not self.active:
            return
        game = self.store.get_game(self.active.exe)
        if not game or not game.region or self.display is None:
            return
        grab = self._make_grab(self.active, game, self.display)
        target = Target(game.title, game.region, game.name_region, game.binarize)
        pipeline = self._pipeline(game.exe)

        async def check() -> bool:
            img = await asyncio.to_thread(grab)
            thumb = thumbnail(img, target.region)
            if not watcher.needs_ocr(thumb):
                return False
            if pipeline is None:
                return True  # перечитать текст нечем: как раньше, по пикселям
            result, name_text = await pipeline.recognize(img, target)
            gone = watcher.line_changed(to_utterance(result, name_text).text, thumb)
            if gone:
                log.info("реплика сменилась (перечитывал текст %s раз)", watcher.checks)
            return gone

        self._watch_pending = True
        fut = self.bg.submit(check())
        fut.add_done_callback(lambda f, t=self.token: self.bridge.watch_done.emit(f, t))

    def _on_watch_done(self, fut: concurrent.futures.Future, token: int) -> None:
        self._watch_pending = False
        if token != self.token:
            return
        try:
            if fut.result():
                # реплика сменилась (игрок кликнул в игру): старый перевод и подчёркивания убираем
                self._close_words("changed")
                self._hide_strip()
                self.current = None
        except Exception:
            log.debug("проверка смены реплики не удалась", exc_info=True)

    # ------------------------------------------------------------------ F3: слова в Lexo
    def _close_words(self, reason: str) -> None:
        if self.word_layer.is_open:
            self.word_layer.finish(reason)

    def f3(self) -> None:
        if self.state != "idle" or not self.active:
            return
        game = self.store.get_game(self.active.exe)
        if game is None:
            return
        if game.region is None:
            self.open_picker()
            return
        if not self.client.linked:
            self.overlay.toast("Слова сохраняются в Lexo: открой LexoPlay из трея и привяжи ПК", 3500)
            return
        if self.ocr_error:
            self.overlay.toast("Нет английского OCR в Windows. Подробности в трее.")
            self._ask_install_ocr()
            return
        display = self._sync_geometry()
        if display is None:
            return
        session = self.sessions.touch(game.exe, game.title)
        cur = self.current
        if (
            cur is not None
            and cur.exe == game.exe
            and (self.overlay.strip_visible or time.time() - cur.at < FRESH_OCR_S)
        ):
            self._open_words(game, session, cur)
            return
        pipeline = self._pipeline(game.exe)
        if pipeline is None:
            return
        self.state = "busy"
        self.token += 1
        token = self.token
        self.watch_timer.stop()
        self.overlay.show_busy(game.region)
        target = Target(game.title, game.region, game.name_region, game.binarize, game.exe, session.id)
        grab = self._make_grab(self.active, game, display)

        def finished(out: Any, err: BaseException | None) -> None:
            if token != self.token:
                return
            self.state = "idle"
            self.overlay.hide_strip()
            if err is not None or out is None:
                log.error("F3 упал", exc_info=err)
                self.overlay.toast(f"Ошибка: {err}")
                return
            scan: ScanOutcome = out
            if not self._game_in_focus() or not self.active:
                return
            if not scan.ok or scan.ocr is None:
                hint = f"Текст не найден. {self.settings.hotkeys.reselect}, чтобы выделить окно диалога заново"
                self.overlay.toast(scan.message or hint)
                return
            line = CurrentLine(game.exe, scan.ocr, scan.utterance, scan.record, time.time(), scan.thumb)
            self.current = line
            g = self.store.get_game(game.exe)
            s = self.sessions.get(game.exe)
            if g and s:
                self._open_words(g, s, line)

        self.run_bg(pipeline.scan(grab, target), finished)

    def _open_words(self, game: Game, session: GameSession, line: CurrentLine) -> None:
        if self.display is None or game.region is None or not line.ocr.words:
            return
        views = build_views(line.ocr, self.vocab.known)
        self.word_ctx = WordCtx(game.exe, game.title, session.id, line, views)
        self.word_layer.font_scale = self.settings.font_scale
        self.word_layer.deck_label = game.deck_label
        self.word_layer.open(self.display, game.region, views, strip=self.overlay.strip_rect())
        self.hotkeys.set_group("words", not self._hotkeys_paused)
        if self.watcher is None:
            self._watch_line(line.sentence, line.thumb)

    def _set_card(self, card: CardState | None) -> None:
        if self.word_ctx is not None:
            self.word_ctx.card = card
        self.word_layer.set_card(card)

    def _card_from(self, sense: Sense) -> CardState:
        return CardState("ready", sense.lemma, sense.ipa, sense.pos, list(sense.senses), in_deck=sense.in_deck)

    def _on_word_selected(self, indices: list) -> None:
        ctx = self.word_ctx
        if ctx is None:
            return
        selection = selected_text(ctx.views, indices)
        if not selection:
            return
        ctx.selection = selection
        ctx.sense = None
        ctx.seq += 1
        seq = ctx.seq
        cached = ctx.lookups.get(selection.lower())
        if cached is not None:
            ctx.sense = cached
            self._set_card(self._card_from(cached))
            return
        self._set_card(CardState("loading", selection))
        coro = self.client.lookup(
            session_id=ctx.session_id,
            game=GameRef(ctx.title, ctx.exe),
            selection=selection,
            sentence=ctx.line.sentence,
            sentence_translation=ctx.line.record.translation if ctx.line.record else None,
        )

        def finished(sense: Any, err: BaseException | None) -> None:
            if self.word_ctx is not ctx or ctx.seq != seq:
                return  # уже выбрали другое слово или вышли из режима
            if err is None:
                ctx.sense = sense
                ctx.lookups[selection.lower()] = sense
                self.lexo_offline = False
                self.outbox.nudge()
                self._set_card(self._card_from(sense))
            elif isinstance(err, LexoOffline):
                self._set_card(
                    CardState("offline", selection, note="Нет связи с Lexo. Слово всё равно сохранится, перевод добавится сам.")
                )
            elif isinstance(err, LexoError) and err.status in (401, 426):
                self._on_lexo_error(err)
            elif isinstance(err, LexoError):
                self._set_card(CardState("error", selection, note=err.message))
            else:
                log.error("lookup упал", exc_info=err)
                self._set_card(CardState("error", selection, note="Не получилось найти значение."))

        self.run_bg(coro, finished)

    def _on_word_add(self) -> None:
        ctx = self.word_ctx
        if ctx is None or not ctx.selection or ctx.card is None or not ctx.card.button_enabled:
            return
        sense = ctx.sense
        record = ctx.line.record
        game = self.store.get_game(ctx.exe)
        deck_mode = game.deck_mode if game else self.settings.deck
        payload = {
            "idempotency_key": str(uuid.uuid4()),
            "session_id": ctx.session_id,
            "game": GameRef(ctx.title, ctx.exe).to_json(),
            "selection": ctx.selection[:80],
            "sentence": ctx.line.sentence[:2000],
            "sentence_translation": record.translation if record else None,
            "lemma": sense.lemma[:80] if sense else None,
            "translation": sense.translation[:200] if sense and sense.translation else None,
            "marked_translation": (sense.marked_translation or None) if sense else None,
            "deck": "current" if deck_mode == "current" else "game",
            "deck_id": game.deck_id if game and deck_mode == "deck" else None,
        }
        self.outbox.enqueue("card", payload)
        word = sense.lemma if sense else ctx.selection
        self.vocab.add(word)
        self.sessions.word_added(ctx.exe)
        in_deck = bool(sense and sense.in_deck)
        card = ctx.card
        deck = game.deck_label if game else ""
        self._set_card(
            CardState(
                "saved",
                card.title,
                card.ipa,
                card.pos,
                list(card.senses),
                in_deck=in_deck,
                note="Пример из игры добавлен к карточке" if in_deck else f"Сохранено в колоду {deck}",
            )
        )
        ctx.views = build_views(ctx.line.ocr, self.vocab.known)
        self.word_layer.set_views(ctx.views)
        self.overlay.toast(f"{word}: сохранено в Lexo")

    def _on_words_finished(self, reason: str) -> None:
        self.word_ctx = None
        self.hotkeys.set_group("words", False)
        log.debug("режим слов закрыт: %s", reason)
        if not self.overlay.strip_visible:
            self.watch_timer.stop()
            self.watcher = None
