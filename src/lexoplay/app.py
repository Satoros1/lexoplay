"""Сборка всего вместе: трей, хоткеи, события окон, оверлей, выделение области, поток F2.

Главный поток = Qt. Захват, OCR и перевод идут в BackgroundLoop, результаты возвращаются
сигналами Bridge. Состояния оверлея: idle -> busy -> idle (строка видна) / picking.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import ctypes
import logging
import os
from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon, QWidget

from lexoplay import __version__
from lexoplay.bg import BackgroundLoop
from lexoplay.config import Settings, data_dir, debug_dir, get_api_key
from lexoplay.debugdump import DebugDump
from lexoplay.dedupe import LineMemory
from lexoplay.geometry import Rect
from lexoplay.hotkeys import HotkeyError, parse
from lexoplay.pipeline import CaptureError, F2Outcome, Pipeline, Target
from lexoplay.preprocess import changed, is_black, thumbnail
from lexoplay.store import Game, Store
from lexoplay.translate import AnthropicTranslator
from lexoplay.ui.overlay import Overlay
from lexoplay.ui.region_picker import RegionPicker
from lexoplay.ui.settings_dialog import SettingsDialog
from lexoplay.ui.theme import app_icon
from lexoplay.win import capture, winapi
from lexoplay.win.hooks import HotkeyManager, WindowEvents
from lexoplay.win.ocr import INSTALL_HINT, OcrUnavailable, WinOcr

log = logging.getLogger(__name__)

GAME_KEYS = ("translate", "words", "hide", "reselect")


def native_place(widget: QWidget, rect: Rect) -> None:
    winapi.place_topmost(int(widget.winId()), rect)


@dataclass
class Active:
    hwnd: int
    pid: int
    exe: str


class Bridge(QObject):
    """Сигналы из фонового потока в поток Qt."""

    f2_done = Signal(object, int)  # Future[F2Outcome], token
    watch_done = Signal(object, int)  # Future[bool], token
    ocr_checked = Signal(object)  # Future[str]


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

        self.ocr = WinOcr("en-US")
        self.ocr_error: str | None = None
        self.translator: AnthropicTranslator | None = None
        self.pipelines: dict[str, Pipeline] = {}
        self.dump = DebugDump(debug_dir())

        self.overlay = Overlay(placer=native_place, on_native=winapi.exclude_from_capture)
        self._apply_overlay_settings()
        self.picker: RegionPicker | None = None

        self.active: Active | None = None
        self.display: Rect | None = None
        self.state = "idle"  # idle | busy | picking
        self.token = 0
        self.shown_thumb = None
        self._watch_pending = False

        self.watch_timer = QTimer(self)
        self.watch_timer.setInterval(400)
        self.watch_timer.timeout.connect(self._watch_tick)
        self.geom_timer = QTimer(self)
        self.geom_timer.setInterval(700)
        self.geom_timer.timeout.connect(self._geom_tick)

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
        self._rebuild_translator()

    # ------------------------------------------------------------------ запуск
    def start(self) -> None:
        self.hotkeys.set_group("global", True)
        self._on_foreground(winapi.foreground_window())
        self.bridge_ocr_check()
        self.tray.show()
        hk = self.settings.hotkeys
        self.tray.showMessage(
            "LexoPlay запущен",
            f"Открой игру и нажми {hk.add_game}, чтобы выделить окно диалога. Потом {hk.translate} переводит реплику.",
            app_icon(),
            6000,
        )
        if not get_api_key():
            QTimer.singleShot(400, lambda: self.open_settings(focus_key=True))

    def shutdown(self) -> None:
        self.hotkeys.clear()
        self.events.close()
        self.overlay.close()
        self.bg.stop()
        self.store.close()

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
        ):
            try:
                self.hotkeys.define(name, parse(text), group)
            except HotkeyError as exc:
                log.warning("хоткей %s=%s: %s", name, text, exc)

    def _apply_overlay_settings(self) -> None:
        self.overlay.font_scale = self.settings.font_scale
        self.overlay.opacity = self.settings.strip_opacity

    def _rebuild_translator(self) -> None:
        key = get_api_key()
        self.translator = AnthropicTranslator(key, self.settings.model) if key else None
        for p in self.pipelines.values():
            if self.translator:
                p.translator = self.translator
            p.target_lang = self.settings.target_lang
            p.source_lang = self.settings.source_lang

    def open_settings(self, focus_key: bool = False) -> None:
        dlg = SettingsDialog(self.settings, self.store, focus_key=focus_key)
        if dlg.exec():
            self.settings = Settings.load()
            self._apply_overlay_settings()
            self._rebuild_translator()
            self._define_hotkeys()
            self.hotkeys.set_group("global", True)
            if self.active:
                self.hotkeys.set_group("game", True)
            self.tray.setToolTip(self._tooltip())

    # ------------------------------------------------------------------ трей
    def _tooltip(self) -> str:
        if self.active:
            g = self.store.get_game(self.active.exe)
            return f"LexoPlay {__version__}: {g.title if g else self.active.exe}"
        return f"LexoPlay {__version__}: нет активной игры"

    def _build_tray(self) -> QSystemTrayIcon:
        tray = QSystemTrayIcon(app_icon(), self)
        menu = QMenu()
        self._status_action = QAction("Нет активной игры", menu)
        self._status_action.setEnabled(False)
        menu.addAction(self._status_action)
        menu.addSeparator()
        menu.addAction("Как пользоваться", self._show_help)
        menu.addAction("Настройки", self.open_settings)
        menu.addAction("Папка с логами", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(data_dir()))))
        menu.addSeparator()
        menu.addAction("Выход", self.qapp.quit)
        tray.setContextMenu(menu)
        tray.setToolTip(self._tooltip())
        tray.activated.connect(
            lambda reason: self.open_settings() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None
        )
        self._menu = menu
        return tray

    def _show_help(self) -> None:
        hk = self.settings.hotkeys
        QMessageBox.information(
            None,
            "LexoPlay",
            f"1. Открой игру и нажми {hk.add_game}: выдели мышкой окно диалога и нажми Enter.\n"
            f"2. В игре жми {hk.translate}: над окном диалога появится перевод. Ещё раз {hk.translate} или {hk.hide} спрячет его.\n"
            f"3. {hk.reselect} выделяет окно диалога заново.\n\n"
            "Игра должна быть в окне или окне без рамки. Для маленьких игр (800×600) используй Magpie, "
            "LexoPlay сам поймёт, что игра растянута.",
        )

    def _set_status(self) -> None:
        g = self.store.get_game(self.active.exe) if self.active else None
        self._status_action.setText(f"Игра: {g.title}" if g else "Нет активной игры")
        self.tray.setToolTip(self._tooltip())

    # ------------------------------------------------------------------ окна
    def _on_foreground(self, hwnd: int) -> None:
        if not hwnd:
            return
        root = winapi.root_window(hwnd)
        pid = winapi.window_pid(root)
        if pid == os.getpid():
            return  # наше окно (выделение области, настройки): состояние не трогаем
        exe = winapi.process_exe(pid)
        game = self.store.get_game(exe) if exe else None
        if game is not None:
            if not self.active or self.active.hwnd != root:
                self._hide_strip()
            self.active = Active(root, pid, game.exe)
            self.events.watch_game(root, pid)
            self.hotkeys.set_group("game", True)
            self.store.touch(game.exe)
            self._sync_geometry()
            self._set_status()
        else:
            self.hotkeys.set_group("game", False)
            self._hide_strip()

    def _on_minimized(self, _hwnd: int, minimized: bool) -> None:
        if minimized:
            self._hide_strip()

    def _on_destroyed(self, _hwnd: int) -> None:
        self._hide_strip()
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
        return rect

    def _geom_tick(self) -> None:
        if self.overlay.isVisible():
            self._sync_geometry()
        else:
            self.geom_timer.stop()

    def _game_in_focus(self) -> bool:
        return bool(self.active) and winapi.root_window(winapi.foreground_window()) == self.active.hwnd

    # ------------------------------------------------------------------ хоткеи
    def _on_hotkey_failed(self, name: str, text: str) -> None:
        self.tray.showMessage(
            "LexoPlay",
            f"Клавиша {text} занята другой программой. Поменяй её в settings.json.",
            QSystemTrayIcon.MessageIcon.Warning,
            5000,
        )

    def _on_hotkey(self, name: str) -> None:
        if self.state == "picking":
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
            self.overlay.toast("Режим слов появится, когда LexoPlay подружится с Lexo")

    def add_current_game(self) -> None:
        hwnd = winapi.root_window(winapi.foreground_window())
        pid = winapi.window_pid(hwnd)
        if not hwnd or pid == os.getpid():
            return
        exe = winapi.process_exe(pid)
        if not exe:
            self.tray.showMessage("LexoPlay", "Не получилось определить игру. Попробуй запустить LexoPlay от имени администратора.")
            return
        if exe in {"explorer.exe", "searchhost.exe", "shellexperiencehost.exe"}:
            return
        game = self.store.get_game(exe) or self.store.save_game(Game(exe, winapi.window_title(hwnd) or exe))
        self.active = Active(hwnd, pid, game.exe)
        self.events.watch_game(hwnd, pid)
        self.hotkeys.set_group("game", True)
        self._set_status()
        self.open_picker()

    # ------------------------------------------------------------------ выделение области
    def open_picker(self) -> None:
        if not self.active:
            return
        display = self._sync_geometry()
        game = self.store.get_game(self.active.exe)
        if display is None or game is None:
            return
        self._hide_strip()
        self.state = "picking"
        picker = RegionPicker(
            display, game.title, game.region, game.name_region, placer=native_place, on_native=winapi.exclude_from_capture
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
        winapi.force_foreground(self.active.hwnd)
        QTimer.singleShot(300, self.f2)  # сразу показать перевод текущей реплики

    def _on_pick_cancelled(self) -> None:
        self.picker = None
        self.state = "idle"
        if self.active:
            winapi.force_foreground(self.active.hwnd)

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
            if not winapi.is_window(hwnd) or winapi.is_minimized(hwnd):
                raise CaptureError("окно игры закрыто или свёрнуто")
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
            self.overlay.toast("Добавь ключ Anthropic в настройках (значок LexoPlay в трее)")
            return
        display = self._sync_geometry()
        if display is None:
            return
        self.state = "busy"
        self.token += 1
        token = self.token
        self.watch_timer.stop()
        self.overlay.show_busy(game.region)
        self.geom_timer.start()
        if self.settings.debug:
            self.dump.new_shot()
        target = Target(game.title, game.region, game.name_region, game.binarize)
        fut = self.bg.submit(pipeline.run(self._make_grab(self.active, game, display), target))
        fut.add_done_callback(lambda f, t=token: self.bridge.f2_done.emit(f, t))

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
        log.info("F2 %s %s", out.kind, {k: round(v, 3) for k, v in out.timings.items()})
        if not self._game_in_focus() or not self.active:
            self._hide_strip()
            return
        game = self.store.get_game(self.active.exe)
        if out.kind in ("translated", "repeat") and out.record and game and game.region:
            self.overlay.show_text(game.region, out.record.translation)
            self.shown_thumb = out.thumb
            self.watch_timer.start()
        elif out.kind == "no_text":
            self._hide_strip()
            self.overlay.toast(f"Текст не найден. {self.settings.hotkeys.reselect}, чтобы выделить область заново")
        else:
            self._hide_strip()
            self.overlay.toast(out.message or "Не получилось перевести", 3500)

    def _hide_strip(self) -> None:
        self.watch_timer.stop()
        self.shown_thumb = None
        if self.state == "busy":
            self.token += 1  # результат пришедшего позже запроса не показываем
            self.state = "idle"
        self.overlay.hide_strip()

    # ------------------------------------------------------------------ смена реплики
    def _watch_tick(self) -> None:
        if not self.overlay.strip_visible or self.state != "idle" or self._watch_pending or not self.active:
            return
        game = self.store.get_game(self.active.exe)
        if not game or not game.region or self.shown_thumb is None or self.display is None:
            return
        grab = self._make_grab(self.active, game, self.display)
        region, before = game.region, self.shown_thumb

        async def check() -> bool:
            img = await asyncio.to_thread(grab)
            return changed(before, thumbnail(img, region), threshold=0.006)

        self._watch_pending = True
        fut = self.bg.submit(check())
        fut.add_done_callback(lambda f, t=self.token: self.bridge.watch_done.emit(f, t))

    def _on_watch_done(self, fut: concurrent.futures.Future, token: int) -> None:
        self._watch_pending = False
        if token != self.token:
            return
        try:
            if fut.result():
                self._hide_strip()  # реплика сменилась: старый перевод убираем
        except Exception:
            pass
