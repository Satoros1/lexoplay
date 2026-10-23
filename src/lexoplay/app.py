"""Сборка всего вместе: окно LexoPlay, трей, клавиши, события окон, оверлей, F2 и F3.

Главный поток = Qt. Захват, OCR и сеть идут в BackgroundLoop, результаты возвращаются
в поток Qt через Bridge. Состояния: idle -> busy -> idle (строка видна) / picking.
Режим слов (F3) живёт в своём окне WordLayer поверх игры и фокус не берёт.

Главное правило: игра не теряет фокус из-за нас. Строка перевода и режим слов фокус не
берут. Выделению области фокус нужен, поэтому оно рисуется поверх стоп-кадра игры, а
после Enter или Esc игра разворачивается и получает фокус обратно.

С привязкой к Lexo перевод идёт через сервер, слова уходят в карточки через outbox,
сессии закрываются сами. Без привязки F2 и F3 работают своим ключом Anthropic: слова
копятся во вкладке «Слова» и сами уходят в Lexo, когда ПК привяжут.

Переведённые реплики хранятся на диске: повтор переводится мгновенно и бесплатно,
а вкладка «История» показывает, что было. Автоперевод (по желанию) сам переводит
следующую реплику, когда игрок пропускает текущую.
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
from datetime import UTC, date, datetime
from typing import Any

from PIL import Image
from PySide6.QtCore import QObject, QPointF, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QFont, QGuiApplication, QImage
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon, QWidget

from lexoplay import __version__, autostart, updater
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
from lexoplay.cursorguard import CursorGuard
from lexoplay.debugdump import DebugDump
from lexoplay.dedupe import LineMemory, LineRecord, normalize_text
from lexoplay.games import NOT_GAMES, WindowInfo, clean_title, is_game_exe
from lexoplay.geometry import Rect
from lexoplay.history import LineCache
from lexoplay.hotkeys import MOD_NOREPEAT, MOD_SHIFT, Hotkey, HotkeyError, parse
from lexoplay.lexo import Account, Deck, GameRef, LexoClient, LexoError, LexoOffline, Sense, Usage
from lexoplay.lookup import (
    AnthropicLookup,
    Explanation,
    explanation_from_json,
    explanation_json,
    lookup_key,
    sense_from_json,
    sense_json,
)
from lexoplay.ocr_model import OcrResult
from lexoplay.ocrscore import Quality
from lexoplay.outbox import Outbox
from lexoplay.pipeline import CaptureError, F2Outcome, Pipeline, ScanOutcome, Target
from lexoplay.preprocess import MODE_NAMES, is_black, thumbnail
from lexoplay.rarity import LEVEL_NAMES, LEVELS
from lexoplay.recall import RecallChip, Recaller, RecallIndex
from lexoplay.session import GameSession, Sessions
from lexoplay.store import Game, SavedWord, Store, StoredLine, Task
from lexoplay.textproc import Utterance, strip_speaker, to_utterance
from lexoplay.translate import (
    AnthropicTranslator,
    ContextLine,
    LexoTranslator,
    TranslateRequest,
    TranslationError,
    Translator,
    check_key,
    drop_label,
    pick_source,
    tidy_saved,
)
from lexoplay.ui.freeze_frame import FreezeFrame
from lexoplay.ui.main_window import AccountStatus, MainWindow, UpdateState
from lexoplay.ui.overlay import Overlay
from lexoplay.ui.region_picker import RegionPicker
from lexoplay.ui.theme import app_icon
from lexoplay.ui.word_layer import WordLayer
from lexoplay.usage import OwnUsage
from lexoplay.vocab import Vocab
from lexoplay.watch import LineWatcher
from lexoplay.win import capture, winapi
from lexoplay.win.hooks import HotkeyManager, WindowEvents
from lexoplay.win.input import InputWatch
from lexoplay.win.llhooks import InputShield, to_local
from lexoplay.win.ocr import INSTALL_HINT, OcrUnavailable, WinOcr
from lexoplay.wordmode import (
    CardState,
    ExplainRow,
    ExplainState,
    WordView,
    game_names,
    match_phrase,
    reader_views,
    selected_text,
)

log = logging.getLogger(__name__)

RELEASES_URL = "https://github.com/Satoros1/lexoplay/releases/latest"
FRESH_OCR_S = 2.0  # F3 берёт распознанную для F2 реплику, если она моложе этого
AUTO_SETTLE_S = 3.0  # автоперевод ждёт, пока реплика допечатается, дольше обычного F2
SETTLE_S = 3.0  # сколько секунд после перевода следим, не допечаталась ли реплика (без действий игрока)
WATCH_IDLE_MS = 400  # как часто смотреть на окно диалога, пока игрок ничего не нажимал
WATCH_HOT_MS = 110  # сразу после клика или клавиши: реплика вот-вот сменится
KICK_MS = 70  # первая проверка после клика: игре нужен кадр-другой, чтобы сменить текст
WATCH_OCR_SLOT = 3  # свой движок OCR для проверок смены реплики (F2 читает слотами 0–2)
WORDS_REPEAT = {"words_left", "words_right", "words_up", "words_down", "words_shift_left", "words_shift_right"}
# клавиши режима слов: Esc, Enter, стрелки, R, 1–5 и Shift (для Shift+стрелка)
WORDS_VKS = {0x1B, 0x0D, 0x25, 0x26, 0x27, 0x28, 0x52, 0x31, 0x32, 0x33, 0x34, 0x35, 0x10, 0xA0, 0xA1}
# игры, которые ловят клик мыши даже без фокуса: на время режима слов их сворачиваем (настройка «Сам решает»)
KNOWN_CLICK_GRABBERS = {"dr1_us.exe", "dr2_us.exe"}
FREEZE_GRACE_S = 1.5  # сколько после сворачивания игры фокус может прыгать сам (в окне)
FREEZE_GRACE_FULL_S = 3.5  # полноэкранной: она ещё меняет режим экрана
PREWARM_MS = 3000  # через столько после запуска окно LexoPlay собирается заранее (если игра не идёт)
NAMES_TTL_S = 600.0  # имена игры пересобираем раз в 10 минут: новые реплики могли добавить новые
CURSOR_TICK_MS = 150  # режим слов: как часто смотреть, не держит ли курсор игра
BUSY_LIMIT_MS = 40_000  # перевод или чтение не вернулись за это время: снять «занято», чтобы F2 снова работал
UPDATE_EVERY_MS = 6 * 3600 * 1000


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
class LastRead:
    """Что LexoPlay последний раз прочитал в окне диалога игры: видно во вкладке «Игры»."""

    image: Image.Image
    text: str
    mode: str
    quality: Quality | None
    at: float

    @property
    def mode_name(self) -> str:
        return MODE_NAMES.get(self.mode, self.mode)


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
    explain: ExplainState | None = None


class Bridge(QObject):
    """Сигналы из фонового потока в поток Qt."""

    f2_done = Signal(object, object)  # Future[F2Outcome], (token, auto)
    watch_done = Signal(object, int)  # Future[bool], token
    ocr_checked = Signal(object)  # Future[str]
    call = Signal(object)  # функция без аргументов: выполнить в потоке Qt


class App(QObject):
    def __init__(
        self,
        qapp: QApplication,
        settings: Settings,
        store: Store,
        bg: BackgroundLoop,
        *,
        quiet: bool = False,
        updated: bool = False,
    ) -> None:
        super().__init__()
        self.quiet = quiet  # запуск вместе с Windows: сразу в трей, без окна и уведомлений
        self.just_updated = updated
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
        self.own_lookup: AnthropicLookup | None = None  # F3 своим ключом, без Lexo
        self.pipelines: dict[str, Pipeline] = {}
        self.dump = DebugDump(debug_dir(), keep=150)
        self.cache = LineCache(store)
        self.own_usage = OwnUsage(store)
        self.last_read: dict[str, LastRead] = {}
        self.update = UpdateState()
        self.auto_live = False  # автоперевод идёт: игрок нажал F2, пока не нажмёт F4
        self._auto_hint = False
        self._busy_source: str | None = None  # реплика, которая сейчас переводится (для оригинала над переводом)
        self._busy_recall: list[RecallChip] = []  # сохранённые слова из неё: подсказки видны уже с точками
        self.recaller = Recaller()
        self._recall_index: RecallIndex | None = None
        self._recall_stamp: tuple[int, int] = (-1, -1)
        self._names: dict[str, tuple[float, set[str]]] = {}  # exe -> (когда собрали, имена игры)
        self._last_input = 0.0  # когда игрок последний раз кликал или жал клавишу (monotonic)
        self._busy_since = 0.0  # когда начался текущий перевод

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
            can_send=lambda: self.client.linked,  # без привязки задачи молча ждут
        )
        self.sessions = Sessions(on_end=self._session_ended)

        self.overlay = Overlay(placer=native_place, on_native=overlay_native)
        self.picker: RegionPicker | None = None
        self.word_layer = WordLayer(placer=native_place, on_native=overlay_native)
        # снимок игры, пока она свёрнута на время режима слов (и пока разворачивается после)
        self.freeze_frame = FreezeFrame(placer=native_place, on_native=overlay_native)
        self.freeze_frame.clicked.connect(lambda: self._close_words("outside"))
        self._apply_overlay_settings()
        self.word_layer.selected.connect(self._on_word_selected)
        self.word_layer.add_clicked.connect(self._on_word_add)
        self.word_layer.explain_clicked.connect(self._request_explain)
        self.word_layer.retranslate_clicked.connect(self._retranslate)
        self._better: AnthropicTranslator | None = None
        self._better_model = ""
        self._translator_sig: tuple | None = None  # с чем собран переводчик: пересобираем, только если это сменилось
        self._retranslate_token: object | None = None
        self.word_layer.explain_save.connect(self._on_explain_save)
        self.word_layer.finished.connect(self._on_words_finished)
        self._explain_cache: dict[tuple[str, str], Explanation] = {}
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
        self.watch_timer.setInterval(WATCH_IDLE_MS)
        self.watch_timer.timeout.connect(self._watch_tick)
        self._kick_timer = QTimer(self)  # проверка вскоре после клика, не дожидаясь очередного тика
        self._kick_timer.setSingleShot(True)
        self._kick_timer.setInterval(KICK_MS)
        self._kick_timer.timeout.connect(self._watch_tick)
        self._busy_timer = QTimer(self)
        self._busy_timer.setSingleShot(True)
        self._busy_timer.setInterval(BUSY_LIMIT_MS)
        self._busy_timer.timeout.connect(self._busy_expired)
        self.geom_timer = QTimer(self)
        self.geom_timer.setInterval(700)
        self.geom_timer.timeout.connect(self._geom_tick)
        self.session_timer = QTimer(self)
        self.session_timer.setInterval(60_000)
        self.session_timer.timeout.connect(self.sessions.tick)
        self.vocab_timer = QTimer(self)
        self.vocab_timer.setInterval(10 * 60_000)
        self.vocab_timer.timeout.connect(lambda: self._refresh_vocab(full=False))
        self.update_timer = QTimer(self)
        self.update_timer.setInterval(UPDATE_EVERY_MS)
        self.update_timer.timeout.connect(lambda: self.check_updates(manual=False))
        self.play_timer = QTimer(self)  # минуты в игре для вкладки «Прогресс»
        self.play_timer.setInterval(60_000)
        self.play_timer.timeout.connect(self._play_tick)

        self.input = InputWatch()  # клики и клавиши игрока: после них перевод может смениться
        self.input.pressed.connect(self._on_user_input)
        self.input.button.connect(self._on_mouse_button)  # средняя и боковые кнопки: на них можно назначить действие
        # режим слов: курсор может держать игра (см. cursorguard.py)
        self._cursor_guard = CursorGuard(winapi.cursor_pos, lambda: self.input.travel, counts=self._cursor_over_game)
        self._cursor_timer = QTimer(self)
        self._cursor_timer.setInterval(CURSOR_TICK_MS)
        self._cursor_timer.timeout.connect(self._cursor_tick)
        self._took_focus = False  # слой слов забрал фокус у игры, чтобы она отпустила курсор
        self._last_words_click = 0.0  # когда хук последний раз отдал нам клик в режиме слов
        self._closing = False  # LexoPlay выходит
        self._frozen = False  # игра свёрнута на время режима слов, на её месте снимок
        self._freeze_full = False  # свернули полноэкранную: после разворота ей нужна секунда-другая
        self._freeze_warned = False
        self._thaw_until = 0.0  # до этих пор игра разворачивается: захват подождёт
        self._thaw_t0 = 0.0
        self._freeze_at = 0.0  # когда свернули: пока игра сворачивается, фокус может прыгнуть куда угодно
        self._refocus_left = 0  # сколько раз ещё вернуть фокус слою, если его увела не игрок
        self._thaw_waiting: set[str] = set()  # что отложено до разворота (F2, F3, выделение): по разу
        self._took_focus_at = 0.0
        # игры, которые от потери фокуса свернулись: фокус у них больше не берём (помним и после перезапуска)
        self._focus_bad: set[str] = set(settings.no_focus_games)
        self._history_sessions: dict[str, str] = {}  # exe -> сессия для разбора и слов из истории (без игры)
        self.hotkeys = HotkeyManager()
        self.hotkeys.triggered.connect(self._on_hotkey)
        self.hotkeys.mouse_changed.connect(self._sync_input)
        self.hotkeys.failed.connect(self._on_hotkey_failed)
        self._define_hotkeys()
        # режим слов: клики над слоем и клавиши режима забираем у игры ещё до окон (win/llhooks.py),
        # иначе игры на Raw Input и DirectInput получают их тоже и пропускают реплику
        self.shield = InputShield()
        self.shield.clicked.connect(self._on_shield_click)
        self.shield.released.connect(self._on_shield_release)
        self.shield.button.connect(self._on_mouse_button)
        self.shield.action.connect(self._on_hotkey)

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
        self._clean_examples()
        QTimer.singleShot(PREWARM_MS, self._prewarm_window)
        self.hotkeys.set_group("global", True)
        self._on_foreground(winapi.foreground_window())
        self.bridge_ocr_check()
        self._outbox_worker = self.bg.submit(self.outbox.run_forever())
        self.session_timer.start()
        self.vocab_timer.start()
        self.play_timer.start()
        self._update_tray()
        self.tray.show()
        if self.client.linked:
            self._check_account()
        autostart.refresh()
        if self.settings.check_updates:
            QTimer.singleShot(8000, lambda: self.check_updates(manual=False))
            self.update_timer.start()
        first_run = not self.store.list_games() or (not self.client.linked and not get_api_key())
        if self.just_updated:
            self.tray.showMessage(
                "LexoPlay обновлён", f"Теперь версия {__version__}. Что нового, видно на странице релиза.", app_icon(), 6000
            )
        elif first_run and not self.quiet:
            page = "lexo" if not self.client.linked and not get_api_key() else "games"
            QTimer.singleShot(200, lambda: self.show_window(page))
        elif not self.quiet:
            hk = self.settings.hotkeys
            self.tray.showMessage(
                "LexoPlay запущен",
                f"В игре {hk.translate} переводит реплику, {hk.words} сохраняет слова. Окно LexoPlay открывается из трея.",
                app_icon(),
                5000,
            )

    def shutdown(self) -> None:
        self._closing = True
        self.update_timer.stop()
        self.play_timer.stop()
        self.word_layer.finish("closed")
        self.sessions.end_all()
        if self.client.linked:  # сессии разборов и слов из истории тоже закрываем
            for sid in self._history_sessions.values():
                self.outbox.enqueue(
                    "end", {"session_id": sid, "duration_s": 0, "lines_count": 0, "ended_at": datetime.now(UTC).isoformat()}
                )
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
        self.shield.close()
        self.input.close()
        self.events.close()
        self.tray.hide()
        self.overlay.close()
        self.word_layer.close()
        self.freeze_frame.close()
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
    def _prewarm_window(self) -> None:
        """Окно LexoPlay собираем заранее, пока игра не запущена: потом оно открывается сразу,
        без задержки на полсекунды. Если игра уже идёт, не трогаем: соберётся, когда откроют."""
        if self.window is not None or self.active is not None or self.state != "idle" or self._closing:
            return
        t0 = time.perf_counter()
        self.window = MainWindow(self)
        log.debug("окно LexoPlay собрано заранее за %.0f мс", (time.perf_counter() - t0) * 1000)

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
            return AccountStatus(False, queue=0, failed=0, note=note, own_key=bool(get_api_key()))
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

    # ------------------------------------------------------------------ прогресс
    def _stat(self, exe: str, *, lines: int = 0, words: int = 0, seconds: int = 0) -> None:
        today = date.today().isoformat()
        before = self.store.stats_days(today).get(today, (0, 0, 0))[1] if words else 0
        try:
            self.store.stats_add(today, exe, lines=lines, words=words, seconds=seconds)
        except Exception:
            log.exception("статистика не записалась")
            return
        goal = self.settings.daily_goal
        if words and goal > 0 and before < goal <= before + words:
            self.overlay.toast(f"Цель на сегодня выполнена: {goal} новых слов", 4000)
            self.tray.showMessage("LexoPlay", f"Цель на сегодня выполнена: {goal} новых слов. Так держать!", app_icon(), 5000)

    def _play_tick(self) -> None:
        if self.active and self._game_in_focus():
            self._stat(self.active.exe, seconds=60)

    def progress(self):  # type: ignore[no-untyped-def]
        from lexoplay.progress import progress

        return progress(self.store, self.settings.daily_goal)

    # ------------------------------------------------------------------ свой ключ
    def usage_summary(self) -> str:
        return self.own_usage.summary(self.settings.model)

    def check_key(self, key: str, done: Callable[[str | None], None]) -> None:
        """Проверить ключ крошечным запросом. done(None) = работает, иначе текст ошибки."""

        def finished(_r: Any, err: BaseException | None) -> None:
            if err is None:
                done(None)
            elif isinstance(err, TranslationError):
                done(str(err))
            else:
                log.error("проверка ключа упала", exc_info=err)
                done(f"Не получилось проверить: {err}")

        self.run_bg(check_key(key, self.settings.model), finished)

    # ------------------------------------------------------------------ история, слова, глоссарий
    def history(self, exe: str | None = None, query: str = "", limit: int = 100, offset: int = 0) -> list[StoredLine]:
        return self.store.lines_list(exe, query, limit, offset)

    def delete_line(self, line_id: int) -> None:
        """Перевод не понравился: убрать из кеша, в следующий раз реплика переведётся заново."""
        self.store.line_delete(line_id)
        for p in self.pipelines.values():
            p.memory.clear()  # иначе повтор возьмётся из памяти сессии

    def clear_history(self, exe: str | None = None) -> None:
        self.store.lines_clear(exe)
        for p in self.pipelines.values():
            p.memory.clear()

    def saved_words(self) -> list[SavedWord]:
        return self.store.words_list()

    def delete_word(self, word_id: int) -> None:
        self.store.word_delete(word_id)

    def export_words(self, path: str, fmt: str) -> int:
        from lexoplay.export import export_words

        return export_words(self.store.words_list(), path, fmt)

    def glossary(self, exe: str) -> dict[str, str]:
        return self.store.glossary(exe)

    def set_glossary(self, exe: str, term: str, translation: str) -> None:
        self.store.glossary_set(exe, term.strip()[:64], translation.strip()[:128])

    def delete_glossary(self, exe: str, term: str) -> None:
        self.store.glossary_delete(exe, term)

    def speakers(self, exe: str) -> list[str]:
        return self.store.line_speakers(exe)

    def get_last_read(self, exe: str) -> LastRead | None:
        return self.last_read.get(exe.lower())

    # ------------------------------------------------------------------ автозапуск и обновления
    def autostart_supported(self) -> bool:
        return autostart.supported()

    def autostart_enabled(self) -> bool:
        return autostart.enabled()

    def set_autostart(self, on: bool) -> bool:
        return autostart.set_enabled(on)

    def check_updates(self, *, manual: bool = True) -> None:
        if getattr(self, "_closing", False) or self.update.phase in ("checking", "downloading", "installing"):
            return
        before = self.update.phase
        self.update.phase = "checking"
        self._update_changed()

        def finished(rel: Any, err: BaseException | None) -> None:
            if err is not None:
                log.info("проверка обновлений упала: %s", err)
                if manual:
                    self.update.phase = "error"
                    self.update.error = str(err) if isinstance(err, updater.UpdateError) else f"Не получилось проверить: {err}"
                else:
                    self.update.phase = before  # фоновая проверка без связи: молча оставляем как было
                self._update_changed()
                return
            self.update.checked_at = time.time()
            self.update.release = rel
            self.update.phase = "available" if rel is not None else "latest"
            self.update.error = None
            self._update_changed()
            if rel is not None and not manual and self.store.get_meta("update_told") != rel.version:
                self.store.set_meta("update_told", rel.version)
                self.tray.showMessage(
                    "LexoPlay: есть новая версия",
                    f"Вышла версия {rel.version}. Обновить можно из меню трея или во вкладке «Ещё», одним нажатием.",
                    app_icon(),
                    8000,
                )

        self.run_bg(updater.check(strict=manual), finished)

    def install_update(self) -> None:
        rel = self.update.release
        if rel is None or self.update.phase in ("downloading", "installing"):
            return
        if not updater.frozen():
            QDesktopServices.openUrl(QUrl(rel.page))
            return
        self.update.phase = "downloading"
        self.update.progress = 0
        self._update_changed()

        def progress(got: int, total: int) -> None:
            pct = int(got * 100 / total) if total else 0
            if pct != self.update.progress:
                self.update.progress = pct
                self.bridge.call.emit(self._update_changed)

        def downloaded(path: Any, err: BaseException | None) -> None:
            if err is not None:
                self.update.phase = "error"
                self.update.error = str(err) if isinstance(err, updater.UpdateError) else f"Не получилось скачать: {err}"
                log.error("обновление не скачалось", exc_info=err)
                self._update_changed()
                return
            self.update.phase = "installing"
            self._update_changed()
            try:
                updater.install(path)
            except updater.UpdateError as exc:
                self.update.phase = "error"
                self.update.error = str(exc)
                self._update_changed()
                return
            self.qapp.quit()  # новый LexoPlay ждёт, пока этот выйдет

        self.run_bg(updater.download(rel, progress), downloaded)

    def _update_changed(self) -> None:
        rel = self.update.release
        if self.update.phase == "available" and rel is not None:
            self._update_action.setText(f"Обновить до {rel.version}")
            self._update_action.setVisible(True)
        elif self.update.phase == "downloading":
            self._update_action.setText(f"Скачиваю обновление… {self.update.progress}%")
            self._update_action.setVisible(True)
        else:
            self._update_action.setVisible(self.update_required)
            self._update_action.setText("Скачать новую версию")
        if self.window is not None:
            self.window.refresh_update()

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
        if not self.settings.auto_translate:
            self.auto_live = False
        self._auto_action.setChecked(self.settings.auto_translate)
        if self.settings.check_updates and not self.update_timer.isActive():
            self.update_timer.start()
        elif not self.settings.check_updates:
            self.update_timer.stop()
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
            ("explain", hk.explain, "game"),
            ("add_game", hk.add_game, "global"),
            ("words_esc", "Escape", "words"),
            ("words_enter", "Enter", "words"),
            # выбор слов без мыши (игра может держать курсор у себя)
            ("words_left", "Left", "words"),
            ("words_right", "Right", "words"),
            ("words_up", "Up", "words"),
            ("words_down", "Down", "words"),
            ("words_shift_left", "Shift+Left", "words"),
            ("words_shift_right", "Shift+Right", "words"),
            ("words_retranslate", "R", "words"),
            *((f"words_save_{n}", str(n), "words") for n in range(1, 6)),
        ):
            if not text:
                continue
            try:
                hk = parse(text)
                if name in WORDS_REPEAT:  # стрелку можно держать: слово за словом
                    hk = Hotkey(hk.mods & ~MOD_NOREPEAT, hk.vk, hk.text)
                self.hotkeys.define(name, hk, group)
            except HotkeyError as exc:
                log.warning("хоткей %s=%s: %s", name, text, exc)
        self._sync_ignored_keys()

    def _hotkey_vks(self) -> set[int]:
        """Клавиши, которые забираем мы: нажатие такой не значит «игрок пропустил реплику».

        Esc и Enter наши только в режиме слов: в самой игре ими часто листают реплики."""
        vks = set()
        for _name, text in self.settings.hotkeys.items():
            try:
                if text:
                    vks.add(parse(text).vk)
            except HotkeyError:
                pass
        if self.word_layer.is_open:
            vks |= WORDS_VKS
        return vks

    def _sync_ignored_keys(self) -> None:
        if hasattr(self, "input"):
            self.input.ignore_vks = self._hotkey_vks()

    def _apply_hotkey_groups(self) -> None:
        paused = self._hotkeys_paused
        self.hotkeys.set_group("global", not paused)
        self.hotkeys.set_group("game", not paused and self._game_in_focus())
        self.hotkeys.set_group("words", not paused and self.word_layer.is_open)

    def _apply_overlay_settings(self) -> None:
        self.overlay.font_scale = self.settings.font_scale
        self.overlay.opacity = self.settings.strip_opacity
        self.overlay.show_original = self.settings.show_original
        self.overlay.strip_place = self.settings.strip_place
        self.overlay.font_family = self.settings.strip_font
        self.overlay.font_bold = self.settings.strip_bold
        self.word_layer.font_scale = self.settings.font_scale
        self.word_layer.font_family = self.settings.strip_font
        self.word_layer.hover_pick = self.settings.hover_pick
        self.word_layer.animate = self.overlay.fade
        self.word_layer.rare_label = LEVEL_NAMES.get(self.settings.rare_level, "")

    def _rebuild_translator(self) -> None:
        """Переводчик под привязку или свой ключ. Пересобирается, только когда сменились
        привязка, ключ, сервер или модель: при сдвиге ползунка в «Виде» соединение с API
        остаётся тёплым, и следующий перевод не ждёт нового рукопожатия."""
        linked = self.client.linked
        key = None if linked else get_api_key()
        think = self.settings.translate_think
        sig = (linked, id(self.client), self.client.server, self.client.token if linked else None, key, self.settings.model, think)
        if sig != self._translator_sig:
            self._translator_sig = sig
            self.own_lookup = None
            self._better = None  # ключ или модель сменились
            if linked:
                self.translator = LexoTranslator(
                    self.client, on_usage=lambda u: self.bridge.call.emit(lambda: self._set_usage(u))
                )
            else:
                self.translator = AnthropicTranslator(key, self.settings.model, think=think) if key else None
                self.own_lookup = AnthropicLookup(key, self.settings.model) if key else None
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
            self._push_local_words()
            self._update_tray()
            done(None)

        self.run_bg(self.client.link(code, self._device_name()), finished)

    def _push_local_words(self) -> None:
        """Слова, сохранённые без Lexo, после привязки сами уходят в карточки."""
        words = self.store.words_list(only_local=True)
        if not words:
            return
        by_game: dict[str, list[SavedWord]] = {}
        for w in reversed(words):  # от старых к новым
            by_game.setdefault(w.exe or "", []).append(w)
        for exe, items in by_game.items():
            game = self.store.get_game(exe) if exe else None
            title = game.title if game else (items[0].game or "Игра")
            deck_mode = game.deck_mode if game else self.settings.deck
            session_id = str(uuid.uuid4())
            for w in items:
                self.outbox.enqueue(
                    "card",
                    {
                        "idempotency_key": f"local-word-{w.id}-{int(w.created_at)}",
                        "session_id": session_id,
                        "game": GameRef(title, exe).to_json(),
                        "selection": w.selection[:80],
                        "sentence": (w.sentence or w.selection)[:2000],
                        "sentence_translation": w.sentence_translation,
                        "lemma": (w.lemma or None) and w.lemma[:80],
                        "translation": (w.translation or None) and w.translation[:200],
                        "marked_translation": None,
                        "deck": "current" if deck_mode == "current" else "game",
                        "deck_id": game.deck_id if game and deck_mode == "deck" else None,
                    },
                )
            self.outbox.enqueue(
                "end",
                {
                    "session_id": session_id,
                    "duration_s": 0,
                    "lines_count": 0,
                    "ended_at": datetime.now(UTC).isoformat(),
                },
            )
        self.store.words_mark_lexo([w.id for w in words])
        log.info("слова без Lexo отправлены в очередь: %s", len(words))
        self.tray.showMessage("LexoPlay", f"Слова, сохранённые до привязки ({len(words)}), отправляются в Lexo.", app_icon(), 5000)

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
                self.check_updates(manual=True)  # сразу найти новую версию: обновится одним нажатием
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
        elif not self.client.linked and self.translator is not None:
            day = self.own_usage.today()
            if day.lines or day.words:
                text += f"\nСвоим ключом сегодня: реплик {day.lines}, слов {day.words}"
        if self.settings.daily_goal > 0:
            today = date.today().isoformat()
            words = self.store.stats_days(today).get(today, (0, 0, 0))[1]
            text += f"\nНовых слов сегодня: {words} из {self.settings.daily_goal}"
        if self.settings.auto_translate:
            text += "\nАвтоперевод включён"
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
        self._auto_action = QAction("Автоперевод реплик", menu)
        self._auto_action.setCheckable(True)
        self._auto_action.setChecked(self.settings.auto_translate)
        self._auto_action.toggled.connect(self._toggle_auto)
        menu.addAction(self._auto_action)
        self._update_action = QAction("Скачать новую версию", menu)
        self._update_action.triggered.connect(self._update_clicked)
        self._update_action.setVisible(False)
        menu.addAction(self._update_action)
        menu.addSeparator()
        menu.addAction("Выход", self.qapp.quit)
        tray.setContextMenu(menu)
        tray.setToolTip(self._tooltip())
        tray.activated.connect(self._on_tray)
        self._menu = menu
        return tray

    def _toggle_auto(self, on: bool) -> None:
        if self.settings.auto_translate == on:
            return
        self.settings.auto_translate = on
        self.settings_changed()
        if self.window is not None:
            self.window.refresh_settings()

    def _update_clicked(self) -> None:
        if self.update.release is not None:
            self.install_update()
        else:
            QDesktopServices.openUrl(QUrl(RELEASES_URL))

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
        self._failed_action.setText(f"Не отправлено: {failed}. Повторить")
        self._failed_action.setVisible(failed > 0 and self.client.linked)
        self._queue_action.setVisible(pending > 0 and self.client.linked)
        self._update_action.setVisible(self.update_required or self.update.phase in ("available", "downloading"))
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
        if self._freezing() and (game is None or (self.active is not None and root == self.active.hwnd)):
            # игру только что свернули: Windows сам отдаёт фокус то ей, то следующему окну
            # (проводнику, другой программе). Это не игрок ушёл: фокус обратно слою, режим не закрываем
            self._refocus_layer(f"{exe or '?'} забрал фокус, пока игра сворачивалась")
            return
        if game is not None:
            if not self.active or self.active.hwnd != root:
                self._hide_strip()
                self._close_words("switch")
            elif self._took_focus and self.word_layer.is_open:
                self._close_words("switch")  # фокус режима слов игрок сам вернул игре (клик по её рамке)
            self.active = Active(root, pid, game.exe)
            self.overlay.strip_custom = game.strip_pos
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

    def _freezing(self) -> bool:
        """Режим слов только что свернул игру (первые секунды)."""
        if not self._frozen or not self.word_layer.is_open:
            return False
        return time.monotonic() - self._freeze_at < (FREEZE_GRACE_FULL_S if self._freeze_full else FREEZE_GRACE_S)

    def _refocus_layer(self, why: str) -> None:
        if self._refocus_left <= 0:
            log.info("режим слов: %s, фокус уже возвращали, больше не трогаю", why)
            return
        self._refocus_left -= 1
        log.info("режим слов: %s, возвращаю фокус слою", why)
        layer = int(self.word_layer.winId())
        winapi.set_no_activate(layer, False)
        winapi.force_foreground(layer)
        self._took_focus = True

    def _on_minimized(self, hwnd: int, minimized: bool) -> None:
        if self._frozen:
            return  # это мы её свернули на время режима слов
        if minimized and self._took_focus and self.active and time.monotonic() - self._took_focus_at < 5:
            # игра свернулась, потому что режим слов забрал фокус: больше так не делаем и разворачиваем её
            log.warning("%s свернулась без фокуса: в режиме слов фокус у неё больше не берём", self.active.exe)
            self._focus_bad.add(self.active.exe)
            self.settings.no_focus_games = sorted(self._focus_bad)
            self.settings.save()
            self._close_words("closed")
            self._return_to_game()
            return
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
        if self._frozen:
            return self.display  # игра свёрнута нами: её рамка сейчас ничего не значит
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
        """Игра в фокусе. Пока фокус на время режима слов у слоя (_words_focus), это тоже считается."""
        if not self.active:
            return False
        root = winapi.root_window(winapi.foreground_window())
        return root == self.active.hwnd or (self._took_focus and root == int(self.word_layer.winId()))

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
        if name.startswith("words_"):
            if self.word_layer.is_open:
                key = name[len("words_") :]
                if key == "retranslate":
                    self.word_layer.request_retranslate()
                elif key.startswith("save_"):
                    self.word_layer.save_explain_item(int(key[len("save_") :]) - 1)
                else:
                    self.word_layer.navigate(key)
            return
        if name == "explain" and self.word_layer.is_open:
            if self.word_layer.explain_open:
                self.word_layer.hide_explain()
            else:
                self._request_explain()
            return
        if self.word_layer.is_open and name in ("translate", "words", "hide"):
            self._close_words("hotkey")
            if name == "hide":
                self._hide_strip()  # F4 прячет всё: и слова, и перевод, и останавливает автоперевод
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
        elif name == "explain":
            self.f3(explain=True)

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
        if self.word_layer.is_open:
            self._close_words("closed")  # и развернуть игру, если режим слов её сворачивал
        if self._after_thaw("picker", self.open_picker):
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
            strip=game.strip_pos,
            strip_place=self.settings.strip_place,
            font_scale=self.settings.font_scale,
        )
        picker.accepted.connect(self._on_picked)
        picker.cancelled.connect(self._on_pick_cancelled)
        self.picker = picker
        picker.open()
        winapi.force_foreground(int(picker.winId()))

    def _on_picked(self, region: Rect, name_region: Rect | None, strip: Rect | None = None) -> None:
        self.picker = None
        self.state = "idle"
        if not self.active:
            return
        game = self.store.get_game(self.active.exe)
        if game:
            self.store.save_game(game.with_(region=region, name_region=name_region, strip_pos=strip))
            self.overlay.strip_custom = strip
            log.info("область диалога %s: %s, плашка перевода: %s", game.exe, region.to_dict(), strip.to_dict() if strip else "по умолчанию")
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
                cache=self.cache,
            )
            self.pipelines[exe] = p
        p._debug = self.dump if self.settings.debug else None  # noqa: SLF001
        return p

    def _target(self, game: Game, session_id: str = "", *, glossary: bool = True) -> Target:
        """glossary=False для проверок смены реплики: они идут часто, а глоссарий им не нужен."""
        assert game.region is not None
        mode = game.ocr_mode or ("pixel" if game.binarize else None)
        return Target(
            game.title,
            game.region,
            game.name_region,
            ocr_mode=mode,
            game_exe=game.exe,
            session_id=session_id,
            glossary=self.store.glossary(game.exe) if glossary else {},
        )

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

    def f2(self, *, auto: bool = False) -> None:
        if self.state != "idle" or not self.active:
            return
        if self._after_thaw("f2", lambda: self.f2(auto=auto)):
            return
        if self.overlay.strip_visible and not auto:
            if self.watcher is None or not self.watcher.acted:
                self._hide_strip()  # ничего не нажимал: F2 просто прячет перевод (и останавливает автоперевод)
                return
            # игрок что-то нажимал, а перевод ещё висит: возможно, реплика уже другая
            self._hide_strip(keep_auto=True)
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
            self.overlay.toast("Перевод не настроен: открой LexoPlay из трея и привяжи Lexo или вставь свой ключ", 3500)
            return
        display = self._sync_geometry()
        if display is None:
            return
        session = self.sessions.touch(game.exe, game.title)
        self._set_busy()
        token = self.token
        self._stop_watch(keep_input=True)
        # клик, пока идёт перевод, тоже считается: реплика могла смениться раньше, чем пришёл перевод
        self._busy_since = time.monotonic()
        self.input.start()
        self.overlay.strip_custom = game.strip_pos
        self.overlay.show_busy(game.region)
        self.geom_timer.start()
        if self.settings.debug:
            self.dump.new_shot()
        target = self._target(game, session.id)
        region = game.region

        self._busy_source = None
        self._busy_recall = []

        def partial(text: str, t: int = token) -> None:
            # из фонового потока: перевод дописывается, показываем как есть
            self.bridge.call.emit(lambda: self._show_partial(t, region, text))

        def source(text: str, t: int = token) -> None:
            self.bridge.call.emit(lambda: self._show_source(t, region, text))

        coro = pipeline.run(
            self._make_grab(self.active, game, display),
            target,
            on_partial=partial,
            settle=AUTO_SETTLE_S if auto else None,
            on_source=source,
        )
        fut = self.bg.submit(coro)
        fut.add_done_callback(lambda f, t=token, a=auto: self.bridge.f2_done.emit(f, (t, a)))

    def _show_source(self, token: int, region: Rect, text: str) -> None:
        """Реплика прочитана, перевод в пути: оригинал (если включён) виден сразу, плашка потом не прыгает."""
        if token == self.token and self.state == "busy":
            self._busy_source = text
            self._busy_recall = self._recall_for(text, commit=False)
            if self.overlay.text is None:
                self.overlay.show_busy(region, original=text, recall=self._busy_recall)

    def _show_partial(self, token: int, region: Rect, text: str) -> None:
        if token == self.token and self.state == "busy" and text:
            self.overlay.show_text(region, text, original=self._busy_source, recall=self._busy_recall)

    def _remember_reading(
        self,
        game: Game,
        mode: str | None,
        quality: Quality | None,
        preview: Image.Image | None,
        text: str,
    ) -> None:
        """Как прочитали окно диалога: для вкладки «Игры» и чтобы в следующий раз читать сразу так же."""
        if preview is not None and mode:
            self.last_read[game.exe] = LastRead(preview, text, mode, quality, time.time())
        if mode and quality is not None and mode != game.ocr_mode and quality.ratio >= 0.6:
            log.info("%s: OCR читает лучше способом %s (%s)", game.exe, mode, quality)
            self.store.save_game(game.with_(ocr_mode=mode, binarize=False))
        if self.window is not None:
            self.window.refresh_games()

    def _set_busy(self) -> None:
        self.state = "busy"
        self.token += 1
        self._busy_timer.start()

    def _set_idle(self) -> None:
        self.state = "idle"
        self._busy_timer.stop()

    def _busy_expired(self) -> None:
        """Сторож: запрос завис (сеть, OCR). Без него F2 и F3 молчали бы до перезапуска."""
        if self.state != "busy":
            return
        log.warning("F2/F3 не вернулись за %s с: снимаем «занято»", self._busy_timer.interval() // 1000)
        self._hide_strip()
        self.overlay.toast("Перевод не пришёл. Нажми ещё раз", 3500)

    def _on_f2_done(self, fut: concurrent.futures.Future, info: tuple[int, bool]) -> None:
        token, auto = info
        if token != self.token:
            return
        self._set_idle()
        try:
            out: F2Outcome = fut.result()
        except Exception as exc:
            log.exception("F2 упал")
            self._hide_strip()
            self.overlay.toast(f"Ошибка: {exc}")
            return
        log.info(
            "F2 %s%s %s %s %s",
            out.kind,
            " (кеш)" if out.cached else "",
            out.code or "",
            out.ocr_mode or "",
            {k: round(v, 3) for k, v in out.timings.items()},
        )
        if not self._game_in_focus() or not self.active:
            self._hide_strip()
            return
        game = self.store.get_game(self.active.exe)
        if game is not None:
            text = out.utterance.text if out.utterance else ""
            self._remember_reading(game, out.ocr_mode, out.quality, out.preview, text)
        own = isinstance(self.translator, AnthropicTranslator)
        if out.kind in ("translated", "repeat") and out.record and game and game.region:
            if out.kind == "translated" or out.cached:
                self.sessions.line_translated(game.exe)
                self._stat(game.exe, lines=1)
            if own and out.kind == "translated":
                self.own_usage.add("line", out.usage, self.settings.model)
            elif own and out.cached:
                self.own_usage.add("cached")
            if out.ocr is not None:
                self.current = CurrentLine(game.exe, out.ocr, out.utterance, out.record, time.time(), out.thumb)
            source = out.utterance.text if out.utterance else out.record.source
            recall = self._recall_for(out.record.source or source, commit=True)
            # старые переводы из кеша бывали с именем говорящего впереди («Байякуя Тогами Судить...»)
            speaker = out.utterance.speaker if out.utterance else None
            shown = tidy_saved(out.record.translation, speaker, out.record.speaker, target_lang=self.settings.target_lang)
            self.overlay.show_text(game.region, shown, original=source, recall=recall)
            self._watch_line(source, out.thumb, settle=True)
            if self.settings.auto_translate:
                self.auto_live = True
                if not self._auto_hint:
                    self._auto_hint = True
                    hk = self.settings.hotkeys
                    self.overlay.toast(f"Автоперевод: следующие реплики переведутся сами. {hk.hide} остановит", 4000)
        elif out.kind == "no_text":
            self._hide_strip(keep_auto=auto)
            if auto and self.auto_live:
                self._watch_wait("", out.thumb)  # реплика ещё не появилась: ждём дальше
            else:
                self.overlay.toast(f"Текст не найден. {self.settings.hotkeys.reselect}, чтобы выделить окно диалога заново")
        elif out.code == "limit_reached" and game and game.region:
            # строка на месте перевода: видно, почему перевода нет; F3 при этом работает
            self.auto_live = False
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
            self._hide_strip()  # ошибка: автоперевод тоже останавливаем, чтобы не биться в неё на каждой реплике
            if out.code == "offline":
                self.lexo_offline = True
                self._update_tray()
            elif out.code == "server_outdated":
                self.server_note = out.message or ""
            self.overlay.toast(out.message or "Не получилось перевести", 4000)

    def _hide_strip(self, *, keep_auto: bool = False) -> None:
        if self.state == "busy":
            self.token += 1  # результат пришедшего позже запроса не показываем
            self._set_idle()
        if not keep_auto:
            self.auto_live = False
        self.overlay.hide_strip()
        if not self.word_layer.is_open:
            self._stop_watch()

    # ------------------------------------------------------------------ смена реплики
    def _watch_line(self, text: str, thumb: Image.Image | None, *, settle: bool = False) -> None:
        """Следить за показанной репликой: перевод уберём, когда игрок её пропустит.

        settle: первые секунды проверять и без действий игрока, не допечаталась ли реплика."""
        if thumb is None:
            self.watcher = None
            return
        gated = self.input.start()  # не включился Raw Input: следим без него, как раньше
        self.watcher = LineWatcher(text, thumb, gated=gated, settle_for=SETTLE_S if settle else 0.0)
        self._start_watch()

    def _watch_wait(self, last_text: str, thumb: Image.Image | None) -> None:
        """Автоперевод: перевода на экране нет, ждём, когда в окне диалога появится новая реплика."""
        if thumb is None or not self.auto_live:
            return
        gated = self.input.start()
        self.watcher = LineWatcher(last_text, thumb, gated=gated, waiting=True)
        self._start_watch()

    def _start_watch(self) -> None:
        self._set_watch_pace(hot=False)
        self.watch_timer.start()
        if self.watcher is not None and self._last_input > self._busy_since:
            self.watcher.arm()  # игрок кликнул, пока шёл перевод: сразу проверить, та ли ещё реплика
            self._watch_soon()

    def _stop_watch(self, *, keep_input: bool = False) -> None:
        self.watch_timer.stop()
        self._kick_timer.stop()
        self.watcher = None
        if not keep_input:
            self._sync_input()

    def _sync_input(self) -> None:
        """Raw Input нужен, пока следим за репликой, открыт режим слов или на кнопку мыши назначено действие."""
        mouse = self.hotkeys.mouse_vks
        self.input.ignore_buttons = mouse
        if self.watcher is not None or self.word_layer.is_open or mouse:
            self.input.start()
        else:
            self.input.stop()

    def _on_mouse_button(self, vk: int) -> None:
        """Средняя или боковая кнопка мыши: если на неё назначено действие, оно срабатывает."""
        if self.hotkeys.mouse_vks:
            self.hotkeys.mouse_pressed(vk, winapi.modifiers_down())

    def _set_watch_pace(self, *, hot: bool) -> None:
        ms = WATCH_HOT_MS if hot else WATCH_IDLE_MS
        if self.watch_timer.interval() != ms:
            self.watch_timer.setInterval(ms)  # перезапускает таймер, поэтому только когда темп меняется

    def _watch_soon(self) -> None:
        """Игрок действовал: проверить реплику через пару кадров и дальше смотреть чаще."""
        if not self.watch_timer.isActive():
            return
        self._set_watch_pace(hot=True)
        if not self._kick_timer.isActive():
            self._kick_timer.start()

    def _on_user_input(self, kind: str = "key") -> None:
        if kind == "mouse" and winapi.cursor_over_own_window():
            return  # клик по словам или разбору реплики: в игру он не попал, реплика та же
        self._last_input = time.monotonic()
        if self.watcher is not None:
            self.watcher.arm()
            self._watch_soon()

    def _watch_tick(self) -> None:
        if self._frozen or time.monotonic() < self._thaw_until:
            return  # игра свёрнута или разворачивается: смотреть не на что, реплика та же
        watcher = self.watcher
        watching = self.overlay.strip_visible or self.word_layer.is_open or bool(watcher and watcher.waiting)
        if not watching or watcher is None or self.state != "idle" or self._watch_pending or not self.active:
            return
        self._set_watch_pace(hot=watcher.hot)
        if not watcher.armed:
            return  # игрок ничего не нажимал: кадр даже не снимаем
        game = self.store.get_game(self.active.exe)
        if not game or not game.region or self.display is None:
            return
        grab = self._make_grab(self.active, game, self.display)
        target = self._target(game, glossary=False)
        pipeline = self._pipeline(game.exe)

        async def check() -> tuple[str | None, Image.Image | None]:
            img = await asyncio.to_thread(grab)
            thumb = thumbnail(img, target.region)
            if not watcher.needs_ocr(thumb):
                return None, None
            if pipeline is None:
                return "gone", thumb  # перечитать текст нечем: как раньше, по пикселям
            reading = await pipeline.recognize(img, target, explore=False, slot=WATCH_OCR_SLOT)
            text = to_utterance(reading.result, reading.name_text).text
            verdict = watcher.check(text, thumb)
            log.debug("проверка реплики: похожесть %s, %r", watcher.last_score, text[:80])
            if verdict:
                log.info(
                    "реплика %s: было %r, стало %r (похожесть %s, проверок %s)",
                    verdict, watcher.norm[:60], text[:60], watcher.last_score, watcher.checks,
                )
            return verdict, thumb

        self._watch_pending = True
        fut = self.bg.submit(check())
        fut.add_done_callback(lambda f, t=self.token: self.bridge.watch_done.emit(f, t))

    def _on_watch_done(self, fut: concurrent.futures.Future, token: int) -> None:
        self._watch_pending = False
        if token != self.token:
            return
        try:
            verdict, thumb = fut.result()
        except Exception:
            log.debug("проверка смены реплики не удалась", exc_info=True)
            return
        if not verdict:
            return
        if self._frozen or time.monotonic() < self._thaw_until:
            return  # кадр снят, пока игру сворачивали или разворачивали: он ничего не говорит о реплике
        last = self.watcher.norm if self.watcher else ""
        auto = self.auto_live and self.settings.auto_translate and self._game_in_focus()
        # реплика сменилась (игрок кликнул в игру): старый перевод и подчёркивания убираем
        self._close_words("changed")
        self.current = None
        if verdict == "grown" and self._game_in_focus():
            # реплика допечаталась: переводим её целиком, а не прячем
            self._hide_strip(keep_auto=True)
            self.f2(auto=auto)
        elif auto and verdict == "new":
            self._hide_strip(keep_auto=True)
            self.f2(auto=True)
        elif auto:
            self._hide_strip(keep_auto=True)
            self._watch_wait(last, thumb)
        else:
            self._hide_strip()

    # ------------------------------------------------------------------ F3: слова в Lexo
    def _close_words(self, reason: str) -> None:
        if self.word_layer.is_open:
            self.word_layer.finish(reason)

    def f3(self, *, explain: bool = False) -> None:
        """Режим слов. explain: сразу открыть разбор реплики (F7)."""
        if self.state != "idle" or not self.active:
            return
        if self._after_thaw("f3", lambda: self.f3(explain=explain)):
            return
        game = self.store.get_game(self.active.exe)
        if game is None:
            return
        if game.region is None:
            self.open_picker()
            return
        if not self.client.linked and self.own_lookup is None:
            self.overlay.toast("Для слов привяжи ПК к Lexo или вставь свой ключ Anthropic: окно LexoPlay → «Ещё»", 4000)
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
            if explain:
                self._request_explain()
            return
        pipeline = self._pipeline(game.exe)
        if pipeline is None:
            return
        self._set_busy()  # проверки смены реплики пока молчат сами (state busy), наблюдатель не трогаем
        token = self.token
        self.overlay.show_busy(game.region)
        target = self._target(game, session.id)
        grab = self._make_grab(self.active, game, display)

        def finished(out: Any, err: BaseException | None) -> None:
            if token != self.token:
                return
            self._set_idle()
            self.overlay.hide_strip()
            if err is not None or out is None:
                log.error("F3 упал", exc_info=err)
                self.overlay.toast(f"Ошибка: {err}")
                return
            scan: ScanOutcome = out
            if not self._game_in_focus() or not self.active:
                return
            g0 = self.store.get_game(game.exe)
            if g0 is not None:
                self._remember_reading(
                    g0, scan.ocr_mode, scan.quality, scan.preview, scan.utterance.text if scan.utterance else ""
                )
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
                if explain:
                    self._request_explain()

        self.run_bg(pipeline.scan(grab, target), finished)

    def _wants_freeze(self, exe: str) -> bool:
        """Сворачивать ли игру на время режима слов (на её месте её снимок).

        «Только где нужно»: игры, которые ловят клик и без фокуса (Danganronpa и те, что поймали
        клик при открытом F3), и полноэкранные: те и так сворачиваются без фокуса, а поверх
        эксклюзивного экрана наша панель может быть не видна вовсе."""
        mode = self.settings.words_freeze
        if mode == "never" or self.active is None:
            return False
        if mode == "always":
            return True
        return exe in KNOWN_CLICK_GRABBERS or exe in self.settings.freeze_games or winapi.exclusive_fullscreen()

    def _freeze_shot(self, game: Game) -> tuple[QImage, Rect] | None:
        """Снимок игры и где его показать: в окне вместе с рамкой и заголовком, иначе там, где игра видна."""
        if self.active is None or self.display is None:
            return None
        hwnd = self.active.hwnd
        full = self._game_fullscreen()
        try:
            if game.capture != "window":
                rect = self.display if full else winapi.visible_rect(hwnd)
                img = capture.grab_screen(rect)
                if not is_black(img):
                    return pil_to_qimage(img), rect
            # снимок экрана чёрный (так бывает у старых DirectX): окно игры через PrintWindow
            return pil_to_qimage(self._make_grab(self.active, game, self.display)()), self.display
        except Exception as exc:
            log.info("снимок для режима слов не снялся, игру не сворачиваем: %s", exc)
            return None

    def _freeze_game(self) -> None:
        """Свернуть игру: свёрнутая, она клик точно не поймает. На её месте её же снимок."""
        if self.active is None or self._frozen:
            return
        self._frozen = True
        self._freeze_full = self._game_fullscreen()
        self._freeze_warned = False
        self._freeze_at = time.monotonic()
        self._refocus_left = 4
        winapi.minimize_window(self.active.hwnd)
        for ms in (250, 1000, 2000):
            QTimer.singleShot(ms, self._check_frozen)
        log.info("режим слов: %s свёрнута, на её месте снимок%s", self.active.exe, " (полноэкранная)" if self._freeze_full else "")

    def _check_frozen(self) -> None:
        """Свернулась ли игра. Полноэкранная, свернувшись, может вернуть монитору его разрешение:
        тогда снимок и панель растягиваем на весь монитор."""
        if not self._frozen or self.active is None or not self.word_layer.is_open:
            return
        hwnd = self.active.hwnd
        layer = int(self.word_layer.winId())
        if winapi.root_window(winapi.foreground_window()) != layer and self._freezing():
            self._refocus_layer("фокус не у слоя")
        if not winapi.is_minimized(hwnd):
            if not self._freeze_warned:
                self._freeze_warned = True
                log.info("режим слов: %s не свернулась (или развернулась сама), снимок просто лежит поверх", self.active.exe)
            return
        if not self._freeze_full or self.display is None:
            return
        mon = winapi.monitor_rect(hwnd)
        d = self.display
        if mon.empty or (abs(mon.x - d.x) <= 2 and abs(mon.y - d.y) <= 2 and abs(mon.w - d.w) <= 2 and abs(mon.h - d.h) <= 2):
            return
        if self.freeze_frame.rect_shown == mon:
            return
        log.info("монитор после сворачивания %s (было %s): снимок и панель на весь монитор", mon.to_dict(), d.to_dict())
        self.freeze_frame.move_to(mon)
        self.word_layer.set_display_rect(mon)  # после снимка: слой снова поверх него

    def _unfreeze(self, reason: str) -> None:
        """Режим закрыт: развернуть игру. Ушёл в другое окно: развернуть, но фокус ей не давать.

        Снимок держится, пока игра разворачивается, чтобы между ними не мелькал рабочий стол."""
        if not self._frozen:
            return
        self._frozen = False
        active = self.active
        if active is None or not winapi.is_window(active.hwnd):
            self.freeze_frame.clear()
            return
        activate = reason not in ("focus", "switch")
        if activate or not self._freeze_full:
            winapi.restore_window(active.hwnd, activate=activate)
        # игрок ушёл в другую программу, а игра полноэкранная: пусть ждёт на панели задач.
        # Развёрнутая без фокуса, она всё равно свернулась бы обратно (или осталась чёрной)
        if activate:
            winapi.force_foreground(active.hwnd)
            for ms in (700, 1600):
                QTimer.singleShot(ms, lambda h=active.hwnd: self._ensure_restored(h))
        if not activate or self._closing:
            self.freeze_frame.clear()  # игрок в другом окне: снимок поверх него не нужен
            self._thaw_until = time.monotonic() + 0.3
        else:
            self._thaw_t0 = time.monotonic()
            self._thaw_until = self._thaw_t0 + (3.0 if self._freeze_full else 1.0)
            QTimer.singleShot(40, self._thaw_tick)
        for ms in (300, 1200):
            QTimer.singleShot(ms, self._sync_geometry)

    def _ensure_restored(self, hwnd: int) -> None:
        """Игру разворачивали с фокусом после режима слов: если она всё ещё свёрнута, ещё раз."""
        if self._frozen or self._closing or not winapi.is_window(hwnd) or not winapi.is_minimized(hwnd):
            return
        if self.active is None or self.active.hwnd != hwnd:
            return
        log.warning("игра после режима слов всё ещё свёрнута: разворачиваю ещё раз")
        winapi.restore_window(hwnd, activate=True)
        winapi.force_foreground(hwnd)

    def _thaw_tick(self) -> None:
        """Игра разворачивается: убрать снимок, как только она снова на экране (или по времени)."""
        if self._frozen or not self.freeze_frame.showing:
            return
        now = time.monotonic()
        hwnd = self.active.hwnd if self.active else 0
        back = bool(hwnd) and winapi.is_window(hwnd) and not winapi.is_minimized(hwnd)
        settle = 0.7 if self._freeze_full else 0.15  # полноэкранной ещё нужно сменить режим экрана
        if (back and now - self._thaw_t0 >= settle) or now >= self._thaw_until:
            self.freeze_frame.clear()
            self._thaw_until = now + 0.15  # пара кадров на перерисовку, потом можно снимать
            self._sync_geometry()
            return
        QTimer.singleShot(50, self._thaw_tick)

    def _after_thaw(self, key: str, fn: Callable[[], None]) -> bool:
        """Игра ещё разворачивается после режима слов: сделать fn потом (снимок сейчас был бы
        переходным, чёрным или с рабочим столом). Нажали дважды: сделаем один раз. True: отложили."""
        left = self._thaw_until - time.monotonic()
        if self._frozen or left <= 0:
            return False
        if key not in self._thaw_waiting:
            self._thaw_waiting.add(key)

            def later() -> None:
                self._thaw_waiting.discard(key)
                fn()

            QTimer.singleShot(int(left * 1000) + 30, later)
        return True

    def _line_views(self, line: CurrentLine) -> list[WordView]:
        """Слова реплики для панели: текст, исправленный моделью при переводе, иначе прочитанный с экрана."""
        level = self.settings.rare_level if self.settings.rare_level in LEVELS else None
        names: set[str] = set()
        if level:
            names = set(self._game_names(line.exe))
            speaker = self._line_speaker(line)
            if speaker:
                names.update(speaker.split())
        return reader_views(line.sentence, self.vocab.known, level=level, names=names)

    def _game_names(self, exe: str) -> set[str]:
        """Имена и названия игры (из её реплик, говорящих и глоссария): их не подсвечиваем как трудные слова."""
        now = time.monotonic()
        cached = self._names.get(exe)
        if cached is not None and now - cached[0] < NAMES_TTL_S:
            return cached[1]
        try:
            names = game_names(self.store.line_texts(exe, 5000), self.store.line_speakers(exe, 200), self.store.glossary(exe))
        except Exception:
            log.exception("имена игры: не получилось собрать")
            names = cached[1] if cached else set()
        self._names[exe] = (now, names)
        return names

    def _recall_for(self, text: str, *, commit: bool) -> list[RecallChip]:
        """Сохранённые слова, которые есть в реплике, для подсказки под переводом. commit: засчитать встречу."""
        if not self.settings.recall_words or not text or not text.strip():
            return []
        try:
            stamp = self.store.words_stamp()
            if self._recall_index is None or stamp != self._recall_stamp:
                self._recall_index = RecallIndex(self.store.words_list(limit=5000))
                self._recall_stamp = stamp
            if not len(self._recall_index):
                return []
            chips, ids = self.recaller.pick(self._recall_index, text, commit=commit)
            if commit and ids:
                self.store.words_seen(ids, time.time())
            return chips
        except Exception:
            log.exception("напоминание о сохранённых словах: упало")
            return []

    @staticmethod
    def _line_speaker(line: CurrentLine) -> str | None:
        if line.record is not None and line.record.speaker:
            return line.record.speaker
        return line.utterance.speaker if line.utterance else None

    def _open_words(self, game: Game, session: GameSession, line: CurrentLine) -> None:
        if self.display is None or game.region is None or not line.ocr.words:
            return
        views = self._line_views(line)
        if not views:
            return
        self.word_ctx = WordCtx(game.exe, game.title, session.id, line, views)
        self.word_layer.font_scale = self.settings.font_scale
        if self.client.linked:
            self.word_layer.deck_label = f"Колода {game.deck_label}"
        else:
            self.word_layer.deck_label = "Сохранится во вкладку «Слова» в LexoPlay"
        self.word_layer.explain_key = self.settings.hotkeys.explain
        self.word_layer.can_retranslate = self.translator is not None
        self.word_layer.retranslating = False
        # перевода нет: панель сразу оставит под него место (_translate_words_line ниже)
        self.word_layer.translating = line.record is None and self.translator is not None
        translation = (
            tidy_saved(line.record.translation, line.record.speaker, target_lang=self.settings.target_lang)
            if line.record is not None
            else None
        )
        self._retranslate_token = None  # перевод прошлой реплики, если ещё пишется, в эту панель не попадёт
        magpie = self._magpie_scaling()
        want = self._wants_freeze(game.exe)
        # игра уже свёрнута (режим открыли заново): снимок прежний, со свёрнутой игры снимать нечего
        shot = self._freeze_shot(game) if want and not magpie and not self._frozen else None
        if shot is not None:
            self.freeze_frame.show_image(*shot)  # сначала снимок: слой слов откроется поверх него
        self.word_layer.open(
            self.display, game.region, views, strip=self.overlay.strip_rect(),
            speaker=self._line_speaker(line), translation=translation,
        )  # fmt: skip
        self.overlay.mute_strip(True)  # перевод теперь в панели: плашка под ней не мигает поверх
        self.hotkeys.set_group("words", not self._hotkeys_paused)
        self._shield_on()
        self._words_focus(force=shot is not None or self._frozen, magpie=magpie)
        if shot is not None:
            self._freeze_game()
        elif want and magpie and not self._frozen:
            self.word_layer.focus_warning = True
            self.word_layer.warning_text = "Magpie: игра не свёрнута, клик может дойти до неё. Наводи на слово, не кликай"
        self._sync_ignored_keys()
        self._cursor_guard.reset()
        self._cursor_timer.start()
        if self.watcher is None or self.watcher.waiting:
            # следим за открытой репликой (автоперевод ждал следующую: теперь ждёт после неё)
            self._watch_line(line.sentence, line.thumb)
        if line.record is None and not self._translate_words_line(again=False):  # перевода нет: панель допишет
            self.word_layer.set_translation(None)

    # ------------------------------------------------------------------ «Перевести иначе»
    def _better_translator(self) -> Translator | None:
        """Своим ключом: модель посильнее (Sonnet) для одной реплики. С Lexo модель выбирает сервер."""
        if self.client.linked:
            return self.translator
        key = get_api_key()
        if not key:
            return None
        model = self.settings.better_model or self.settings.model
        if self._better is None or self._better_model != model:
            self._better = AnthropicTranslator(key, model, think=self.settings.translate_think)
            self._better_model = model
        return self._better

    def _retranslate(self) -> None:
        """Перевод реплики не понравился: перевести заново, показав модели прошлый вариант."""
        self._translate_words_line(again=True)

    def _translate_words_line(self, *, again: bool) -> bool:
        """Перевести реплику, открытую в режиме слов. again: «Перевести иначе» (модель посильнее,
        видит прошлый перевод); иначе перевода у реплики ещё не было, и панель его допишет.
        False: перевести нечем (нет ключа, игры, области)."""
        ctx = self.word_ctx
        if ctx is None or (again and self.word_layer.retranslating):
            return False
        game = self.store.get_game(ctx.exe)
        translator = self._better_translator() if again else self.translator
        if game is None or game.region is None or translator is None:
            return False
        line = ctx.line
        record = line.record
        text = line.utterance.text if line.utterance else (record.source if record else line.sentence)
        speaker = line.utterance.speaker if line.utterance else (record.speaker if record else None)
        pipeline = self._pipeline(ctx.exe)
        target = self._target(game, ctx.session_id)
        earlier = [r for r in (pipeline.context_for(target, text) if pipeline else []) if r.norm != normalize_text(text)]
        region = game.region
        token = object()
        self._retranslate_token = token

        def partial(t: str) -> None:
            self.bridge.call.emit(lambda: self._retranslate_partial(token, ctx, region, t, text, again))

        req = TranslateRequest(
            text=text,
            speaker=speaker,
            game=game.title,
            target_lang=self.settings.target_lang,
            source_lang=self.settings.source_lang,
            context=tuple(ContextLine(r.speaker, r.source, r.translation) for r in earlier),
            glossary=target.glossary,
            line_id=str(uuid.uuid4()),
            session_id=ctx.session_id,
            game_exe=ctx.exe,
            previous=record.translation if record and again else None,
            on_partial=partial,
        )
        if again:
            self.word_layer.retranslating = True
            self.word_layer.update()
        else:
            self.word_layer.set_translation(None, partial=True)
        own = isinstance(translator, AnthropicTranslator)
        model = (self._better_model if again else self.settings.model) if own else ""

        def done(res: Any, err: BaseException | None) -> None:
            current = self._retranslate_token is token
            if current:
                self._retranslate_token = None
                self.word_layer.retranslating = False
                self.word_layer.translating = False
                self.word_layer.update()
            here = current and self.word_ctx is ctx  # на экране всё ещё эта реплика
            if err is not None:
                if not isinstance(err, TranslationError):
                    log.error("перевод в режиме слов: упало", exc_info=err)
                if here:
                    self.overlay.toast(str(err) if isinstance(err, TranslationError) else "Не получилось перевести", 4000)
                    if record is not None and (again or self.overlay.strip_visible):
                        self.overlay.show_text(region, record.translation, original=text, recall=self.overlay.recall)
                    self.word_layer.set_translation(record.translation if record else None)
                return
            rec = LineRecord(req.line_id, speaker, pick_source(text, res.source), res.translation, normalize_text(text))
            if pipeline is not None:
                pipeline.memory.replace(rec)
            self.cache.retranslated(ctx.exe, self.settings.target_lang, rec)
            self._forget_explain(ctx.exe, self._explain_norm(line))  # разбор говорил о старом переводе
            line.record = rec
            ctx.explain = None
            if model:
                self.own_usage.add("line", res.usage, model)
            if not again:  # новая переведённая реплика: в сессию и в «Прогресс», как от F2
                self.sessions.line_translated(ctx.exe)
                self._stat(ctx.exe, lines=1)
            if not here:
                return  # перевод сохранён, а на экране уже другая реплика или режим закрыт
            if self.overlay.strip_visible or again:
                self.overlay.show_text(region, res.translation, original=text, recall=self.overlay.recall)
            if self.word_layer.is_open:
                self.word_layer.set_explain(None, show=False)
                self.word_layer.set_translation(res.translation)
                if not self.word_layer.selection and rec.source != text:
                    ctx.views = self._line_views(line)  # модель поправила, что прочитал OCR
                    self.word_layer.set_views(ctx.views)
            if again:
                self.overlay.toast("Переведено заново" + (f" ({model})" if model else ""), 2500)
                log.info("перевели иначе: %r -> %r", record.translation if record else None, res.translation)

        self.run_bg(translator.translate(req), done)
        return True

    def _retranslate_partial(
        self, token: object, ctx: WordCtx, region: Rect, text: str, original: str | None = None, again: bool = True
    ) -> None:
        if token is not self._retranslate_token or self.word_ctx is not ctx or not text:
            return
        if again or self.overlay.strip_visible:
            self.overlay.show_text(region, text, original=original, recall=self.overlay.recall)
        if self.word_layer.is_open:
            self.word_layer.set_translation(text, partial=True)

    # ------------------------------------------------------------------ разбор реплики (F7)
    def _request_explain(self) -> None:
        ctx = self.word_ctx
        if ctx is None or not self.word_layer.is_open:
            return
        if ctx.explain is not None and ctx.explain.phase == "ready":
            self.word_layer.set_explain(ctx.explain)
            return
        norm = self._explain_norm(ctx.line)
        cached = self._saved_explain(ctx.exe, norm)
        if cached is not None:
            ctx.explain = self._explain_state(ctx, cached)
            self.word_layer.set_explain(ctx.explain)
            return
        if ctx.explain is not None and ctx.explain.phase == "loading":
            self.word_layer.set_explain(ctx.explain)
            return
        if not self.client.linked and self.own_lookup is None:
            self.word_layer.set_explain(
                ExplainState("error", note="Для разбора привяжи ПК к Lexo или вставь свой ключ Anthropic во вкладке «Ещё».")
            )
            return
        ctx.explain = ExplainState("loading")
        self.word_layer.set_explain(ctx.explain)

        def finished(result: Any, err: BaseException | None) -> None:
            if err is None:
                self._remember_explain(ctx.exe, norm, result)  # за разбор уже заплачено: сохраняем, даже если слова закрыли
            if self.word_ctx is not ctx:
                return
            if err is None:
                ctx.explain = self._explain_state(ctx, result)
            else:
                ctx.explain = ExplainState("error", note=self._explain_error(err))
            if self.word_layer.is_open:
                self.word_layer.set_explain(ctx.explain, show=self.word_layer.explain_open)

        self.run_bg(self._explain_call(ctx), finished)

    def _explain_error(self, err: BaseException) -> str:
        if isinstance(err, LexoError) and err.status in (401, 426):
            self._on_lexo_error(err)
        if not isinstance(err, (LexoError, LexoOffline, TranslationError)):
            log.error("разбор реплики упал", exc_info=err)
        return (
            err.message if isinstance(err, LexoError) else
            "Нет связи с Lexo." if isinstance(err, LexoOffline) else
            str(err) if isinstance(err, TranslationError) else "Не получилось разобрать реплику."
        )

    @staticmethod
    def _explain_norm(line: CurrentLine) -> str:
        """Ключ разбора: как реплика записана в историю."""
        return line.record.norm if line.record is not None and line.record.norm else normalize_text(line.sentence)

    def _saved_explain(self, exe: str, norm: str) -> Explanation | None:
        """Разбор из памяти, а после перезапуска с диска."""
        got = self._explain_cache.get((exe, norm))
        if got is None:
            try:
                raw = self.store.explain_get(exe, self.settings.target_lang, norm)
            except Exception:
                log.exception("разборы на диске недоступны")
                raw = None
            got = explanation_from_json(raw) if raw else None
            if got is not None:
                self._remember_explain(exe, norm, got, save=False)
        return got

    def _remember_explain(self, exe: str, norm: str, exp: Explanation, *, save: bool = True) -> None:
        self._explain_cache[(exe, norm)] = exp
        if len(self._explain_cache) > 100:
            self._explain_cache.pop(next(iter(self._explain_cache)))
        if save:
            try:
                self.store.explain_put(exe, self.settings.target_lang, norm, explanation_json(exp))
            except Exception:
                log.exception("разбор не сохранился на диск")

    def _forget_explain(self, exe: str, norm: str) -> None:
        self._explain_cache.pop((exe, norm), None)
        try:
            self.store.explain_delete(exe, self.settings.target_lang, norm)
        except Exception:
            log.exception("разбор не удалился с диска")

    async def _explain_call(self, ctx: WordCtx) -> Explanation:
        line = ctx.line
        record = line.record
        speaker = line.utterance.speaker if line.utterance else (record.speaker if record else None)
        translation = record.translation if record else None
        pipeline = self.pipelines.get(ctx.exe)
        norm = self._explain_norm(line)
        earlier = [(r.speaker, r.source, r.translation) for r in (pipeline.memory.context(6) if pipeline else []) if r.norm != norm]
        return await self._explain_fetch(ctx.exe, ctx.title, ctx.session_id, line.sentence, speaker, translation, earlier)

    async def _explain_fetch(
        self,
        exe: str,
        title: str,
        session_id: str,
        sentence: str,
        speaker: str | None,
        translation: str | None,
        earlier: list[tuple[str | None, str, str]],
    ) -> Explanation:
        """Разбор реплики: через Lexo, а без привязки своим ключом."""
        if self.client.linked or self.own_lookup is None:
            return await self.client.explain(
                session_id=session_id,
                game=GameRef(title, exe),
                sentence=sentence,
                speaker=speaker,
                translation=translation,
                context=[{"speaker": s, "source": src, "translation": tr} for s, src, tr in earlier],
            )
        lookup = self.own_lookup
        result = await lookup.explain(
            sentence=sentence,
            speaker=speaker,
            translation=translation,
            game=title,
            context=[(s, src) for s, src, _tr in earlier],
            target_lang=self.settings.target_lang,
            source_lang=self.settings.source_lang,
        )
        self.own_usage.add("explain", lookup.last_usage, self.settings.model)
        return result

    # ------------------------------------------------------------------ история: разбор, «перевести иначе», слова
    def _earlier_in_history(self, line: StoredLine, limit: int) -> list[StoredLine]:
        """Реплики игры прямо перед этой (за час): контекст для разбора и перевода из истории."""
        return self.store.lines_range(line.exe, line.seen_at - 3600, line.seen_at - 0.001, limit=limit)[0]

    def line_explanation(self, line: StoredLine) -> Explanation | None:
        return self._saved_explain(line.exe, line.norm)

    def explain_history_line(self, line: StoredLine, done: Callable[[Explanation | None, str | None], None]) -> None:
        """Кнопка «Разбор» в истории: сохранённый разбор сразу, иначе запрос (Lexo или свой ключ)."""
        saved = self.line_explanation(line)
        if saved is not None:
            done(saved, None)
            return
        if not self.client.linked and self.own_lookup is None:
            done(None, "Для разбора привяжи ПК к Lexo или вставь свой ключ Anthropic во вкладке «Ещё».")
            return
        game = self.store.get_game(line.exe)
        title = game.title if game else line.exe
        earlier = [(r.speaker, r.source, r.translation) for r in self._earlier_in_history(line, 6)]

        def finished(result: Any, err: BaseException | None) -> None:
            if err is None:
                self._remember_explain(line.exe, line.norm, result)
                done(result, None)
            else:
                done(None, self._explain_error(err))

        coro = self._explain_fetch(
            line.exe, title, self._history_session(line.exe), line.source, line.speaker, line.translation, earlier
        )
        self.run_bg(coro, finished)

    def _history_session(self, exe: str) -> str:
        """Сессия для запросов из истории: идущая сейчас игровая или одна на игру до выхода из LexoPlay."""
        live = self.sessions.get(exe)
        if live is not None:
            return live.id
        sid = self._history_sessions.get(exe)
        if sid is None:
            sid = self._history_sessions[exe] = str(uuid.uuid4())
        return sid

    def retranslate_history_line(self, line: StoredLine, done: Callable[[str | None, str | None], None]) -> None:
        """«Перевести иначе» из истории: моделью посильнее, с репликами перед этой и прошлым вариантом."""
        translator = self._better_translator()
        if translator is None:
            done(None, "Перевести заново можно с привязкой к Lexo или своим ключом Anthropic.")
            return
        game = self.store.get_game(line.exe)
        earlier = self._earlier_in_history(line, max(4, self.settings.context_lines))
        req = TranslateRequest(
            text=line.source,
            speaker=line.speaker,
            game=game.title if game else line.exe,
            target_lang=self.settings.target_lang,
            source_lang=self.settings.source_lang,
            context=tuple(ContextLine(r.speaker, r.source, r.translation) for r in earlier),
            glossary=self.store.glossary(line.exe),
            line_id=str(uuid.uuid4()),
            session_id=self._history_session(line.exe),
            game_exe=line.exe,
            previous=line.translation,
        )
        model = self._better_model if isinstance(translator, AnthropicTranslator) else ""

        def finished(res: Any, err: BaseException | None) -> None:
            if err is not None:
                if not isinstance(err, TranslationError):
                    log.error("перевести иначе из истории: упало", exc_info=err)
                done(None, str(err) if isinstance(err, TranslationError) else "Не получилось перевести заново.")
                return
            rec = LineRecord(req.line_id, line.speaker, res.source, res.translation, line.norm)
            self.cache.retranslated(line.exe, self.settings.target_lang, rec, touch=False)
            pipeline = self.pipelines.get(line.exe)
            if pipeline is not None:
                pipeline.memory.replace(rec)
            self._forget_explain(line.exe, line.norm)
            if model:
                self.own_usage.add("line", res.usage, model)
            log.info("перевели иначе из истории: %r -> %r", line.translation, res.translation)
            done(res.translation, None)

        self.run_bg(translator.translate(req), finished)

    def save_phrase(self, line: StoredLine, phrase: str, meaning: str, base: str = "") -> str:
        """«+» в разборе из истории: фраза в слова (словарной формой, если она есть). Возвращает, куда ушла."""
        game = self.store.get_game(line.exe)
        title = game.title if game else line.exe
        self._store_word(
            line.exe, title, self._history_session(line.exe),
            selection=phrase, lemma=base or phrase, translation=meaning, ipa=None, pos=None,
            sentence=line.source, sentence_translation=line.translation,
        )
        return "Lexo" if self.client.linked else "«Слова»"

    def _explain_state(self, ctx: WordCtx, exp: Explanation) -> ExplainState:
        rows = [ExplainRow(i.phrase, i.meaning, i.note, match_phrase(ctx.views, i.phrase), base=i.base) for i in exp.items]
        return ExplainState("ready", rows, exp.summary, literal=exp.literal)

    def _on_explain_save(self, index: int) -> None:
        """«+» у пункта разбора: фраза уходит в слова (в Lexo или во вкладку «Слова»)."""
        ctx = self.word_ctx
        if ctx is None or ctx.explain is None or not 0 <= index < len(ctx.explain.rows):
            return
        row = ctx.explain.rows[index]
        if row.saved:
            return
        self._save_word(ctx, selection=row.phrase, lemma=row.lemma, translation=row.meaning, ipa=None, pos=None)
        row.saved = True
        self.word_layer.set_explain(ctx.explain)
        where = "Lexo" if self.client.linked else "«Слова»"
        self.overlay.toast(f"{row.lemma}: сохранено в {where}")

    def _save_word(
        self,
        ctx: WordCtx,
        *,
        selection: str,
        lemma: str | None,
        translation: str | None,
        ipa: str | None,
        pos: str | None,
        marked: str | None = None,
    ) -> None:
        """Сохранить слово или фразу из реплики: локально всегда, в Lexo, если ПК привязан."""
        record = ctx.line.record
        self._store_word(
            ctx.exe, ctx.title, ctx.session_id,
            selection=selection, lemma=lemma, translation=translation, ipa=ipa, pos=pos,
            sentence=ctx.line.sentence, sentence_translation=record.translation if record else None, marked=marked,
        )
        self.sessions.word_added(ctx.exe)
        ctx.views = self._line_views(ctx.line)
        self.word_layer.set_views(ctx.views)

    def _clean_example(self, exe: str, sentence: str, translation: str | None) -> tuple[str, str | None]:
        """Пример к слову без имени говорящего («Byakuya Togami So, you...») ни в оригинале, ни в переводе."""
        clean = strip_speaker(sentence, self.store.line_speakers(exe, 300) if exe else [])
        return clean, drop_label(translation, clean) if translation else translation

    def _clean_examples(self) -> None:
        """Слова, сохранённые до 0.6.14: в примерах бывало имя говорящего. Чистим один раз."""
        if self.store.get_meta("examples_clean") == "1":
            return
        fixed = 0
        try:
            for w in self.store.words_list(limit=100_000):
                if not w.sentence:
                    continue
                sentence, translation = self._clean_example(w.exe, w.sentence, w.sentence_translation)
                if (sentence, translation) != (w.sentence, w.sentence_translation):
                    self.store.word_set_example(w.id, sentence, translation)
                    fixed += 1
            self.store.set_meta("examples_clean", "1")
        except Exception:
            log.exception("не получилось почистить примеры к словам")
        if fixed:
            log.info("примеры к словам без имени говорящего: %s", fixed)

    def word_line(self, w: SavedWord) -> StoredLine:
        """Реплика, из которой слово: для разбора во вкладке «Слова» (тот же, что в «Истории»)."""
        lang = self.settings.target_lang
        norm = normalize_text(w.sentence or "")
        found = self.store.line_find(w.exe, lang, norm) if w.exe and norm else None
        if found is not None:
            return found
        return StoredLine(
            id=-w.id, exe=w.exe or "", norm=norm, speaker=None, source=w.sentence or "",
            translation=w.sentence_translation or "", lang=lang, created_at=w.created_at, seen_at=w.created_at, hits=0,
        )  # fmt: skip

    def _store_word(
        self,
        exe: str,
        title: str,
        session_id: str,
        *,
        selection: str,
        lemma: str | None,
        translation: str | None,
        ipa: str | None,
        pos: str | None,
        sentence: str,
        sentence_translation: str | None,
        marked: str | None = None,
    ) -> None:
        sentence, sentence_translation = self._clean_example(exe, sentence, sentence_translation)
        game = self.store.get_game(exe)
        linked = self.client.linked
        self.store.word_add(
            SavedWord(
                exe=exe,
                game=title,
                selection=selection[:80],
                lemma=(lemma or None) and lemma[:80],
                translation=(translation or None) and translation[:200],
                ipa=ipa,
                pos=pos,
                sentence=sentence[:2000],
                sentence_translation=sentence_translation,
                created_at=time.time(),
                lexo=linked,
            )
        )
        if linked:
            deck_mode = game.deck_mode if game else self.settings.deck
            self.outbox.enqueue(
                "card",
                {
                    "idempotency_key": str(uuid.uuid4()),
                    "session_id": session_id,
                    "game": GameRef(title, exe).to_json(),
                    "selection": selection[:80],
                    "sentence": sentence[:2000],
                    "sentence_translation": sentence_translation,
                    "lemma": (lemma or None) and lemma[:80],
                    "translation": (translation or None) and translation[:200],
                    "marked_translation": marked,
                    "deck": "current" if deck_mode == "current" else "game",
                    "deck_id": game.deck_id if game and deck_mode == "deck" else None,
                },
            )
        self.vocab.add(lemma or selection)
        self._stat(exe, words=1)

    def _set_card(self, card: CardState | None) -> None:
        if self.word_ctx is not None:
            self.word_ctx.card = card
        self.word_layer.set_card(card)

    def _card_from(self, sense: Sense) -> CardState:
        return CardState(
            "ready", sense.lemma, sense.ipa, sense.pos, list(sense.senses), in_deck=sense.in_deck, local=not self.client.linked
        )

    async def _lookup(self, ctx: WordCtx, selection: str) -> Sense:
        """Значение слова: через Lexo, а без привязки своим ключом."""
        translation = ctx.line.record.translation if ctx.line.record else None
        if self.client.linked or self.own_lookup is None:
            return await self.client.lookup(
                session_id=ctx.session_id,
                game=GameRef(ctx.title, ctx.exe),
                selection=selection,
                sentence=ctx.line.sentence,
                sentence_translation=translation,
            )
        lookup = self.own_lookup
        s = self.settings
        key = lookup_key(s.model, s.target_lang, s.source_lang, selection, ctx.line.sentence, translation)
        raw = self.store.lookup_get(key)
        sense = sense_from_json(raw, selection) if raw else None
        if sense is None:
            sense = await lookup.lookup(
                selection=selection,
                sentence=ctx.line.sentence,
                sentence_translation=translation,
                target_lang=s.target_lang,
                source_lang=s.source_lang,
            )
            self.own_usage.add("word", lookup.last_usage, s.model)
            try:
                self.store.lookup_put(key, sense_json(sense))
            except Exception:
                log.exception("значение слова не сохранилось на диск")
        known = self.vocab.known(sense.lemma) or self.vocab.known(selection)
        return Sense(sense.lemma, sense.pos, sense.ipa, list(sense.senses), sense.marked_translation, in_deck=known)

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
        local = not self.client.linked
        self._set_card(CardState("loading", selection, local=local))
        coro = self._lookup(ctx, selection)

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
            elif isinstance(err, TranslationError):
                note = str(err) + " Слово можно сохранить и без перевода."
                self._set_card(CardState("error", selection, note=note, local=local))
            else:
                log.error("lookup упал", exc_info=err)
                self._set_card(CardState("error", selection, note="Не получилось найти значение."))

        self.run_bg(coro, finished)

    def _on_word_add(self) -> None:
        ctx = self.word_ctx
        if ctx is None or not ctx.selection or ctx.card is None or not ctx.card.button_enabled:
            return
        sense = ctx.sense
        self._save_word(
            ctx,
            selection=ctx.selection,
            lemma=sense.lemma if sense else None,
            translation=sense.translation if sense and sense.translation else None,
            ipa=sense.ipa if sense else None,
            pos=sense.pos if sense else None,
            marked=(sense.marked_translation or None) if sense else None,
        )
        word = sense.lemma if sense else ctx.selection
        card = ctx.card
        if self.client.linked:
            game = self.store.get_game(ctx.exe)
            in_deck = bool(sense and sense.in_deck)
            deck = game.deck_label if game else ""
            note = "Пример из игры добавлен к карточке" if in_deck else f"Сохранено в колоду {deck}"
            self._set_card(CardState("saved", card.title, card.ipa, card.pos, list(card.senses), in_deck=in_deck, note=note))
            self.overlay.toast(f"{word}: сохранено в Lexo")
        else:
            note = "Сохранено во вкладку «Слова». После привязки к Lexo уйдёт в карточки само"
            self._set_card(CardState("saved", card.title, card.ipa, card.pos, list(card.senses), note=note, local=True))
            self.overlay.toast(f"{word}: сохранено в «Слова»")

    def _cursor_tick(self) -> None:
        """Режим слов открыт: не держит ли курсор игра (мышь двигают, а он стоит)."""
        if not self.word_layer.is_open:
            self._cursor_timer.stop()
            return
        if self._cursor_guard.locked:
            winapi.release_cursor()  # игра держит курсор: пока открыт режим слов, снимаем её зажим
            return
        if not self._cursor_guard.tick():
            return
        winapi.release_cursor()
        self.word_layer.cursor_locked = True
        self.word_layer.update()
        exe = self.active.exe if self.active else "?"
        if self._took_focus:
            log.info("курсор держит игра %s даже без фокуса: слова стрелками", exe)
        elif not self._words_focus():  # игра без фокуса обычно отпускает курсор
            log.info("курсор держит игра %s: слова стрелками, фокус не забираем", exe)

    def _words_focus(self, *, force: bool = False, magpie: bool | None = None) -> bool:
        """Режим слов открыт: фокус на это время у слоя, а не у игры.

        Многие игры читают мышь и клавиатуру через Raw Input или DirectInput. Это хук не
        останавливает, и клик по слову листал бы реплику. Без фокуса игре такой ввод просто
        не приходит. Не берём фокус, если игра уже сворачивалась от этого или её растягивает
        Magpie (он бы перестал)."""
        if self._took_focus:
            return True
        if self.active is None or not self.word_layer.is_open:
            return False
        exe = self.active.exe
        why = ""
        if magpie is None:
            magpie = self._magpie_scaling()
        if exe in self._focus_bad and self._game_fullscreen() and not force:
            why = "она сворачивалась без фокуса"  # в окне не свернётся; сворачиваем сами: тоже берём
        elif magpie and not winapi.magpie_modern():
            why = "игру растягивает Magpie старше 0.12: без фокуса он перестал бы растягивать"
        if why:
            log.info("режим слов без фокуса у %s: %s", exe, why)
            self.word_layer.focus_warning = True
            return False
        if winapi.exclusive_fullscreen():
            # полноэкранная игра может свернуться без фокуса: тогда _on_minimized развернёт её
            # и запомнит, что фокус у неё брать нельзя
            log.info("режим слов: %s в полноэкранном режиме, фокус всё равно берём", exe)
        hwnd = int(self.word_layer.winId())
        winapi.set_no_activate(hwnd, False)
        winapi.force_foreground(hwnd)
        self._took_focus = True
        self._took_focus_at = time.monotonic()
        QTimer.singleShot(150, lambda: self._check_words_focus(retry=True))
        return True

    def _check_words_focus(self, *, retry: bool) -> None:
        """Взялся ли фокус на самом деле: если нет, ещё раз, а потом честная подсказка в панели."""
        if not self.word_layer.is_open or not self._took_focus:
            return
        layer = int(self.word_layer.winId())
        fg = winapi.root_window(winapi.foreground_window())
        if fg == layer:
            log.info("режим слов: фокус у слоя, игра ввод не получает")
            return
        who = winapi.process_exe(winapi.window_pid(fg)) if fg else None
        if self._frozen:
            # игра свёрнута: клик до неё не дойдёт, пугать подсказкой незачем. Фокус всё равно возвращаем
            # (клавиши режима нужны слою), пока игра сворачивается
            if retry or self._freezing():
                self._refocus_layer(f"фокус у {who}")
                QTimer.singleShot(300, lambda: self._check_words_focus(retry=False))
            return
        if retry:
            log.info("режим слов: фокус не взялся (в фокусе %s), пробую ещё раз", who)
            winapi.force_foreground(layer)
            QTimer.singleShot(200, lambda: self._check_words_focus(retry=False))
            return
        log.warning("режим слов: фокус так и не взялся (в фокусе %s), клик может дойти до игры", who)
        self.word_layer.focus_warning = True
        self.word_layer.update()

    def _game_fullscreen(self) -> bool:
        """Игра на весь монитор (полноэкранный режим), а не в окне."""
        if self.active is None:
            return False
        try:
            w, m = winapi.window_rect(self.active.hwnd), winapi.monitor_rect(self.active.hwnd)
        except Exception:
            return False
        return w.x <= m.x + 2 and w.y <= m.y + 2 and w.right >= m.right - 2 and w.bottom >= m.bottom - 2

    def _magpie_scaling(self) -> bool:
        if self.active is None or self.settings.magpie_mode == "never":
            return False
        try:
            return winapi.find_magpie_window(winapi.monitor_rect(self.active.hwnd)) is not None
        except Exception:
            log.debug("не понял, растянута ли игра Magpie", exc_info=True)
            return False

    def _cursor_over_game(self, pos: tuple[int, int]) -> bool:
        """Курсор над игрой и не упёрт в край экрана: только тогда «стоит» значит «держит игра»."""
        d = self.display
        if d is None or not (d.x <= pos[0] < d.x + d.w and d.y <= pos[1] < d.y + d.h):
            return False
        return not winapi.at_screen_edge(pos)

    def _give_focus_back(self, reason: str) -> None:
        """Режим слов закрыт: фокус обратно игре (если его забирали и игрок не ушёл в другое окно)."""
        if not self._took_focus:
            return
        self._took_focus = False
        winapi.set_no_activate(int(self.word_layer.winId()), True)
        if reason not in ("focus", "switch", "closed"):
            self._return_to_game()

    def _shield_on(self) -> None:
        """Режим слов открыт: клики над слоем и клавиши режима больше не доходят до игры."""
        keys = {} if self._hotkeys_paused else self.hotkeys.group_keys("words")
        if not self.shield.start(int(self.word_layer.winId()), keys, WORDS_REPEAT):
            log.warning("хуки режима слов не встали (%s): клики могут дойти до игры", self.shield.failed)

    def _on_shield_click(self, vk: int, x: int, y: int, mods: int) -> None:
        """Клик над слоем слов, который хук не пустил в игру: отвечаем на него сами."""
        layer = self.word_layer
        self._last_words_click = time.monotonic()
        if not layer.is_open:
            return
        lx, ly = to_local(int(layer.winId()), x, y, layer.devicePixelRatioF())
        layer.click(QPointF(lx, ly), right=vk == 2, shift=bool(mods & MOD_SHIFT))

    def _on_shield_release(self, vk: int) -> None:
        if self.word_layer.is_open:
            self.word_layer.release()

    def _on_words_finished(self, reason: str) -> None:
        if reason == "changed" and time.monotonic() - self._last_words_click < 2.0:
            fg = winapi.root_window(winapi.foreground_window())
            exe = self.active.exe if self.active else ""
            log.warning(
                "реплика сменилась сразу после клика в режиме слов: фокус у слоя %s, взяли фокус %s, игра на весь экран %s, %s",
                fg == int(self.word_layer.winId()), self._took_focus, self._game_fullscreen(), exe or "?",
            )  # fmt: skip
            if exe and self.settings.words_freeze == "auto" and exe not in self.settings.freeze_games:
                # игра ловит клик мимо LexoPlay: дальше на время режима слов сворачиваем её
                self.settings.freeze_games = sorted({*self.settings.freeze_games, exe})
                self.settings.save()
                self.overlay.toast("Игра поймала клик. Дальше на время F3 LexoPlay будет сворачивать её", 5000)
        self.word_ctx = None
        self._unfreeze(reason)
        # плашку возвращаем чуть позже: если реплика сменилась, её уберут раньше, и старая не мелькнёт
        QTimer.singleShot(0, lambda: None if self.word_layer.is_open else self.overlay.mute_strip(False))
        self.shield.stop()
        self.hotkeys.set_group("words", False)
        self._sync_ignored_keys()
        self._cursor_timer.stop()
        self._give_focus_back(reason)
        log.debug("режим слов закрыт: %s", reason)
        if self.overlay.strip_visible:
            return
        watcher = self.watcher
        keep = reason in ("esc", "outside", "idle", "hotkey")
        if watcher is not None and keep and self.auto_live and self.settings.auto_translate:
            self._watch_wait(watcher.norm, watcher.baseline)  # автоперевод: ждём следующую реплику дальше
        else:
            self._stop_watch()
