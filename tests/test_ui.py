"""UI без экрана (QT_QPA_PLATFORM=offscreen): выделение области и оверлей."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from lexoplay.geometry import Rect  # noqa: E402
from lexoplay.ui.overlay import Overlay  # noqa: E402
from lexoplay.ui.region_picker import RegionPicker  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_picker_drag_and_enter(qapp):
    picker = RegionPicker(Rect(0, 0, 800, 600), "Test Game")
    picker.open()
    got = {}
    picker.accepted.connect(lambda r, n, s: got.update(region=r, name=n))
    QTest.mousePress(picker, Qt.MouseButton.LeftButton, pos=QPoint(40, 450))
    QTest.mouseMove(picker, QPoint(760, 580))
    QTest.mouseRelease(picker, Qt.MouseButton.LeftButton, pos=QPoint(760, 580))
    QTest.keyClick(picker, Qt.Key.Key_Tab)
    QTest.mousePress(picker, Qt.MouseButton.LeftButton, pos=QPoint(40, 420))
    QTest.mouseRelease(picker, Qt.MouseButton.LeftButton, pos=QPoint(200, 445))
    QTest.keyClick(picker, Qt.Key.Key_Return)
    r = got["region"]
    assert abs(r.x - 0.05) < 0.01 and abs(r.y - 0.75) < 0.01 and abs(r.w - 0.9) < 0.01
    assert got["name"] is not None and got["name"].h < 0.06


def test_picker_tiny_drag_ignored_and_escape(qapp):
    picker = RegionPicker(Rect(0, 0, 800, 600), "Test Game")
    picker.open()
    events = []
    picker.accepted.connect(lambda *a: events.append("ok"))
    picker.cancelled.connect(lambda: events.append("cancel"))
    QTest.mousePress(picker, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
    QTest.mouseRelease(picker, Qt.MouseButton.LeftButton, pos=QPoint(12, 12))
    QTest.keyClick(picker, Qt.Key.Key_Return)  # области нет: Enter ничего не делает
    QTest.keyClick(picker, Qt.Key.Key_Escape)
    assert events == ["cancel"]


def test_overlay_states(qapp):
    ov = Overlay()
    ov.set_display_rect(Rect(0, 0, 960, 720))
    assert not ov.isVisible()
    ov.show_busy(Rect(0.05, 0.78, 0.9, 0.18))
    assert ov.isVisible() and ov.strip_visible
    ov.show_text(Rect(0.05, 0.78, 0.9, 0.18), "Привет")
    ov.hide_strip()
    assert not ov.strip_visible  # логически скрыта сразу, а дорисовывается, пока исчезает
    QTest.qWait(400)
    assert not ov.isVisible()
    ov.toast("тост")
    assert ov.isVisible()
    ov.close()


def test_overlay_fades_and_busy_dots_run(qapp):
    ov = Overlay()
    ov.set_display_rect(Rect(0, 0, 960, 720))
    region = Rect(0.05, 0.78, 0.9, 0.18)
    ov.show_busy(region)
    assert ov._alpha < 0.5  # появляется плавно
    QTest.qWait(700)
    assert ov._alpha > 0.99
    assert ov._dots_timer.isActive() and ov._dots in (1, 2)  # точки бегут, пока идёт перевод
    ov.show_text(region, "Перевод")
    assert not ov._dots_timer.isActive()
    ov.hide_strip()
    assert ov._ghost is not None and ov.isVisible()
    ov.show_text(region, "Следующая реплика")  # новая реплика, пока старая ещё исчезала
    assert ov._ghost is None and ov.strip_visible
    QTest.qWait(300)
    assert ov._alpha > 0.99
    ov.fade = False
    ov.hide_strip()
    assert not ov.isVisible()  # без плавности окно прячется сразу
    ov.close()


# ---------------------------------------------------------------- режим слов и настройки
from lexoplay.ocr_model import Line, OcrResult, Word  # noqa: E402
from lexoplay.ui.word_layer import WordLayer  # noqa: E402
from lexoplay.wordmode import CardState, build_views  # noqa: E402


def _ocr_line(text, y=0.85):
    words, x = [], 0.08
    for w in text.split():
        words.append(Word(w, Rect(x, y, 0.012 * len(w), 0.035)))
        x += 0.012 * len(w) + 0.012
    return Line(tuple(words))


def test_word_layer_arrow_keys_select_words_and_phrases(qapp):
    from lexoplay.wordmode import ExplainRow, ExplainState, next_word, word_in_line

    res = OcrResult((_ocr_line("Don't jump to conclusions.", 0.80), _ocr_line("Look at the evidence first.", 0.86)))
    ocr_views = build_views(res)
    assert next_word(ocr_views, None, 1) == 0 and next_word(ocr_views, 0, -1) == 0  # у края стоим
    assert word_in_line(ocr_views, 1, 1) in (5, 6) and word_in_line(ocr_views, 4, -1) in (0, 1)  # ближайшее по горизонтали
    from lexoplay.wordmode import reader_views

    # панель раскладывает реплику сама: длинная переносится на несколько строк
    views = reader_views("Don't jump to conclusions. Look at the evidence first, then decide what really happened here.")
    layer = WordLayer()
    picked, saves, again = [], [], []
    layer.selected.connect(picked.append)
    layer.explain_save.connect(saves.append)
    layer.retranslate_clicked.connect(lambda: again.append(1))
    layer.open(Rect(0, 0, 1000, 700), Rect(0.05, 0.75, 0.9, 0.2), views)
    layer.navigate("right")  # первое слово
    layer.navigate("right")
    assert layer.selection == [1] and picked == []  # значение спросим, когда выбор остановится
    QTest.qWait(450)
    assert picked == [[1]]
    layer.navigate("shift_right")
    layer.navigate("shift_right")
    QTest.qWait(450)
    assert picked[-1] == [1, 2, 3]  # «jump to conclusions»
    layer.navigate("down")
    lv = layer.views
    assert len(layer.selection) == 1 and lv[layer.selection[0]].rect.y > lv[3].rect.y  # строкой ниже
    layer.can_retranslate = True
    layer.request_retranslate()
    assert again == [1]
    layer.set_explain(ExplainState("ready", [ExplainRow("jump to conclusions", "спешить с выводами", base="")], "Совет"))
    layer.save_explain_item(0)
    layer.save_explain_item(3)  # такого пункта нет
    assert saves == [0]
    layer.grab()
    layer.finish("esc")
    assert not layer.is_open and not layer._blank  # noqa: SLF001  пустой кадр нарисован и сброшен
    layer.close()


def test_word_layer_click_select_and_add(qapp):
    res = OcrResult((_ocr_line("This place feels eerie after dark."),))
    views = build_views(res, lambda w: w.lower().startswith("eerie"))
    layer = WordLayer()
    picked, added, done = [], [], []
    layer.selected.connect(picked.append)
    layer.add_clicked.connect(lambda: added.append(1))
    layer.finished.connect(done.append)
    layer.open(Rect(0, 0, 1000, 700), Rect(0.05, 0.8, 0.9, 0.15), views)
    qapp.processEvents()
    views = layer.views  # рамки слов ставит панель, когда раскладывает реплику
    eerie = views[3]
    cx = int((eerie.rect.x + eerie.rect.w / 2) * layer.width())
    cy = int((eerie.rect.y + eerie.rect.h / 2) * layer.height())
    QTest.mouseClick(layer, Qt.MouseButton.LeftButton, pos=QPoint(cx, cy))
    assert picked == [[3]]
    after = views[4]
    ax = int((after.rect.x + after.rect.w / 2) * layer.width())
    QTest.mouseClick(layer, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, QPoint(ax, cy))
    assert picked[-1] == [3, 4]
    layer.set_card(CardState("ready", "eerie", "ˈɪəri", "adj", ["жуткий", "зловещий"]))
    img = layer.grab().toImage()
    assert layer._button_rect is not None
    b = layer._button_rect.center()
    QTest.mouseClick(layer, Qt.MouseButton.LeftButton, pos=QPoint(int(b.x()), int(b.y())))
    QTest.keyClick(layer, Qt.Key.Key_Return)
    assert added == [1, 1]
    layer.set_card(CardState("loading", "eerie"))
    QTest.keyClick(layer, Qt.Key.Key_Return)
    assert added == [1, 1]  # пока значение грузится, добавлять нечего
    QTest.keyClick(layer, Qt.Key.Key_Escape)
    assert done == ["esc"] and not layer.is_open
    assert img.width() == 1000
    layer.close()
    layer.deleteLater()
    qapp.processEvents()


def test_word_layer_clicks_from_hook(qapp):
    """Клики, которые хук забрал у игры, приходят в click(): тот же выбор, что и мышью по окну."""
    res = OcrResult((_ocr_line("This place feels eerie after dark."),))
    views = build_views(res)
    layer = WordLayer()
    picked, done = [], []
    layer.selected.connect(picked.append)
    layer.finished.connect(done.append)
    layer.click(QPointF(10, 10))  # режим ещё закрыт: ничего
    assert done == [] and picked == []
    layer.open(Rect(0, 0, 1000, 700), Rect(0.05, 0.8, 0.9, 0.15), views)
    qapp.processEvents()
    views = layer.views

    def center(v):  # type: ignore[no-untyped-def]
        return QPointF((v.rect.x + v.rect.w / 2) * layer.width(), (v.rect.y + v.rect.h / 2) * layer.height())

    layer.click(center(views[3]))
    layer.click(center(views[4]), shift=True)
    assert picked == [[3], [3, 4]]
    layer.click(center(views[3]), right=True)  # правая кнопка закрывает режим, но когда её отпустят:
    assert done == [] and layer.is_open  # иначе отпускание досталось бы игре
    layer.release()
    assert done == ["outside"] and not layer.is_open
    layer.click(center(views[2]))  # запоздалый клик после закрытия
    assert picked == [[3], [3, 4]]
    layer.close()
    layer.deleteLater()
    qapp.processEvents()


def test_word_layer_hover_picks_and_saves_without_clicks(qapp):
    """Игры, которые ловят клик и без фокуса: слово выбирается наведением, «+» тоже держат курсором."""
    from lexoplay.wordmode import reader_views

    layer = WordLayer()
    picked, added = [], []
    layer.selected.connect(picked.append)
    layer.add_clicked.connect(lambda: added.append(1))
    layer.open(Rect(0, 0, 1000, 700), Rect(0.05, 0.8, 0.9, 0.15), reader_views("This place feels eerie after dark."))
    qapp.processEvents()
    v = layer.views

    def center(w):  # type: ignore[no-untyped-def]
        return QPointF((w.rect.x + w.rect.w / 2) * layer.width(), (w.rect.y + w.rect.h / 2) * layer.height())

    layer.hover(center(v[1]))
    layer.hover(center(v[3]))  # проскочил «place»: его не выбираем
    QTest.qWait(600)
    assert picked == [[3]]
    layer.set_card(CardState("ready", "eerie", "ˈɪəri", "adj", ["жуткий"]))
    layer.grab()  # кнопка «+» нарисована
    b = layer._button_rect.center()  # noqa: SLF001
    layer.hover(QPointF(b.x(), b.y()))
    QTest.qWait(400)
    layer.grab()  # кнопка заполняется
    assert added == []
    QTest.qWait(700)
    assert added == [1]
    layer.hover(QPointF(b.x() + 1, b.y()))  # курсор чуть сдвинулся, но всё ещё на «+»
    QTest.qWait(1100)
    assert added == [1]  # второй раз то же слово не сохраняется
    layer.hover_pick = False  # выключено в настройках: только клики и клавиши
    layer.hover(center(v[4]))
    QTest.qWait(600)
    assert picked == [[3]]
    layer.close()
    layer.deleteLater()
    qapp.processEvents()


def test_freeze_frame_shows_moves_and_closes_on_click(qapp):
    from PySide6.QtGui import QColor, QImage

    from lexoplay.ui.freeze_frame import FreezeFrame

    img = QImage(64, 36, QImage.Format.Format_RGB32)
    img.fill(QColor("#b0281e"))
    frame = FreezeFrame()
    clicks = []
    frame.clicked.connect(lambda: clicks.append(1))
    frame.show_image(img, Rect(10, 10, 640, 360))
    qapp.processEvents()
    assert frame.showing and frame.rect_shown == Rect(10, 10, 640, 360)
    shot = frame.grab().toImage()
    assert shot.pixelColor(shot.width() // 2, shot.height() // 2).red() > 150  # снимок растянут на окно
    frame.move_to(Rect(0, 0, 800, 450))  # полноэкранная свернулась: монитор вернул разрешение
    assert frame.rect_shown == Rect(0, 0, 800, 450)
    QTest.mouseClick(frame, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
    assert clicks == [1]
    frame.clear()
    assert not frame.showing
    frame.close()


class FakeCtl:
    """Контроллер для окна LexoPlay без Windows и сервера."""

    def __init__(self, store, settings) -> None:
        from lexoplay.ui.main_window import UpdateState

        self.store, self.settings = store, settings
        self.linked = False
        self.codes = []
        self.saved = 0
        self.paused = []
        self.picked = []
        self.key = ""
        self.update = UpdateState()
        self.installs = 0
        self.checks = 0
        self.autostart = False
        self.last_reads = {}
        self.explained_lines = []
        self.saved_phrases = []

    def account_status(self):
        from lexoplay.ui.main_window import AccountStatus

        if self.linked:
            return AccountStatus(True, "@ruslan", used=3, limit=50)
        return AccountStatus(False, queue=2)

    def link(self, code, done):
        self.codes.append(code)
        if code == "BADBAD":
            done("Код не подошёл.")
        else:
            self.linked = True
            done(None)

    def unlink(self, done):
        self.linked = False
        done()

    def load_decks(self, done):
        from lexoplay.lexo import Deck

        done([Deck(5, "Слова из игр", 12, 3, 1)], 5, None)

    def create_deck(self, title, done):
        from lexoplay.lexo import Deck

        done(Deck(9, title), None)

    def list_windows(self):
        return []

    def add_game(self, w):
        pass

    def pick_region(self, exe):
        self.picked.append(exe)

    def forget_game(self, exe):
        self.store.delete_game(exe)

    def save_game(self, g):
        self.store.save_game(g)

    def active_exe(self):
        return "dr1_us.exe"

    def settings_changed(self):
        self.saved += 1

    def pause_hotkeys(self, paused):
        self.paused.append(paused)

    def api_key(self):
        return self.key

    def set_api_key(self, k):
        self.key = k

    def retry_failed(self):
        pass

    def make_report(self):
        return None

    def open_logs(self):
        pass

    def window_hidden(self):
        pass

    def usage_summary(self):
        return "Сегодня реплик: 3. Это примерно меньше цента."

    def check_key(self, key, done):
        done(None if key.startswith("sk-ant-") else "Ключ Anthropic не подходит.")

    def history(self, exe=None, query="", limit=100, offset=0):
        return self.store.lines_list(exe, query, limit, offset)

    def explain_history_line(self, line, done):
        from lexoplay.lookup import ExplainItem, Explanation

        self.explained_lines.append(line.id)
        done(Explanation((ExplainItem("class trial", "классный суд", "термин игры"),), "Торжественно."), None)

    def retranslate_history_line(self, line, done):
        self.store.line_retranslated(line.exe, "ru", line.norm, "Новый перевод.", 3, touch=False)
        done("Новый перевод.", None)

    def save_phrase(self, line, phrase, meaning, base=""):
        self.saved_phrases.append(base or phrase)
        return "«Слова»"

    def delete_line(self, line_id):
        self.store.line_delete(line_id)

    def clear_history(self, exe=None):
        self.store.lines_clear(exe)

    def saved_words(self):
        return self.store.words_list()

    def word_line(self, w):
        from lexoplay.store import StoredLine

        return StoredLine(-w.id, w.exe, w.sentence or "", None, w.sentence or "", w.sentence_translation or "", "ru", 0.0, 0.0, 0)

    def delete_word(self, word_id):
        self.store.word_delete(word_id)

    def export_words(self, path, fmt):
        from lexoplay.export import export_words

        return export_words(self.store.words_list(), path, fmt)

    def glossary(self, exe):
        return self.store.glossary(exe)

    def set_glossary(self, exe, term, tr):
        self.store.glossary_set(exe, term, tr)

    def delete_glossary(self, exe, term):
        self.store.glossary_delete(exe, term)

    def speakers(self, exe):
        return self.store.line_speakers(exe)

    notes_busy = False
    notes_remade = 0

    def game_notes(self, exe):
        return self.store.game_notes(exe, "ru")

    def notes_state(self, exe):
        return self.notes_busy, ""

    def notes_possible(self):
        return True

    def set_game_notes(self, exe, text):
        self.store.game_notes_set(exe, "ru", text, "ready" if text else "off", edited=True)

    def remake_game_notes(self, exe):
        self.notes_remade += 1
        self.notes_busy = True
        return True

    def get_last_read(self, exe):
        return self.last_reads.get(exe)

    def autostart_supported(self):
        return True

    def autostart_enabled(self):
        return self.autostart

    def set_autostart(self, on):
        self.autostart = on
        return True

    def check_updates(self, *, manual=True):
        self.checks += 1

    def install_update(self):
        self.installs += 1

    def progress(self):
        from lexoplay.progress import progress

        return progress(self.store, self.settings.daily_goal)


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("LEXOPLAY_HOME", str(tmp_path))
    from lexoplay.config import Settings
    from lexoplay.store import Game, Store
    from lexoplay.ui.main_window import MainWindow

    store = Store(tmp_path / "local.db")
    store.save_game(Game("dr1_us.exe", "Danganronpa", region=Rect(0.05, 0.7, 0.9, 0.25)))
    ctl = FakeCtl(store, Settings())
    w = MainWindow(ctl)
    yield w, ctl
    w.hide()
    w.deleteLater()
    qapp.processEvents()
    store.close()


def test_main_window_link_flow(window, qapp):
    from PySide6.QtWidgets import QLabel

    w, ctl = window
    w.show_page("lexo")
    page = w.lexo
    QTest.keyClicks(page.code, "badbad")
    assert page.code.text() == "BADBAD"
    QTest.keyClick(page.code, Qt.Key.Key_Return)
    assert ctl.codes == ["BADBAD"] and "не подошёл" in page.link_status.text()
    page.code.setText("K7QX2M")
    page.link_btn.click()
    assert ctl.linked
    texts = [lbl.text() for lbl in page.account.findChildren(QLabel)]
    assert "@ruslan" in texts and "3 из 50" in texts
    decks = [lbl.text() for lbl in page.table.findChildren(QLabel)]
    assert any("Слова из игр" in t for t in decks)
    assert "Lexo: @ruslan" in w.account_chip.text()


def test_games_page_suggests_name_frame_when_speakers_are_missing(window, qapp):
    from PySide6.QtWidgets import QLabel

    w, ctl = window

    def tip_shown():
        w.show_page("games")
        return any("Совет: имя говорящего" in lbl.text() for lbl in w.games.findChildren(QLabel))

    for i in range(10):
        ctl.store.line_add("dr1_us.exe", "ru", f"n{i}", None, f"Line {i}", f"Реплика {i}")
    assert not tip_shown()  # мало реплик: рано судить
    for i in range(10, 20):
        ctl.store.line_add("dr1_us.exe", "ru", f"n{i}", None, f"Line {i}", f"Реплика {i}")
    assert tip_shown()
    game = ctl.store.get_game("dr1_us.exe")
    ctl.store.save_game(game.with_(name_region=Rect(0.05, 0.62, 0.2, 0.05)))
    assert not tip_shown()  # рамка имени уже есть


def test_games_page_deck_choice_and_pick(window, qapp):
    w, ctl = window
    ctl.linked = True
    w.lexo.reload_decks()
    w.show_page("games")
    from PySide6.QtWidgets import QComboBox, QPushButton

    combos = w.games.findChildren(QComboBox)
    decks = [c for c in combos if isinstance(c.itemData(0), tuple)]
    assert decks, "у привязанного ПК у игры есть выбор колоды"
    combo = decks[0]
    idx = next(i for i in range(combo.count()) if combo.itemData(i) and combo.itemData(i)[0] == "deck")
    combo.activated.emit(idx)
    g = ctl.store.get_game("dr1_us.exe")
    assert g.deck_mode == "deck" and g.deck_id == 5 and g.deck_title == "Слова из игр"
    pick = next(b for b in w.games.findChildren(QPushButton) if "Выделить" in b.text())
    pick.click()
    assert ctl.picked == ["dr1_us.exe"]
    # текст на весь экран (NVL): переключатель у игры
    mode = next(c for c in w.games.findChildren(QComboBox) if c.itemData(0) == "dialog")
    assert mode.currentData() == "dialog"
    mode.setCurrentIndex(1)
    assert ctl.store.get_game("dr1_us.exe").text_mode == "page"


def test_keys_page_capture_and_conflict(window, qapp):
    w, ctl = window
    w.show_page("keys")
    edit = w.keys.edits["reselect"]
    edit.click()
    assert ctl.paused[-1] is True and edit.text() == "Нажми клавишу…"
    QTest.keyClick(edit, Qt.Key.Key_F7)
    assert ctl.settings.hotkeys.reselect == "F7" and ctl.paused[-1] is False and ctl.saved == 1
    words = w.keys.edits["words"]
    words.click()
    QTest.keyClick(words, Qt.Key.Key_F7)  # та же клавиша, что у «выделить заново»
    assert "стоит на" in w.keys.notes["words"].text() and "стоит на" in w.keys.notes["reselect"].text()
    ctrl = w.keys.edits["hide"]
    ctrl.click()
    QTest.keyClick(ctrl, Qt.Key.Key_F4, Qt.KeyboardModifier.ControlModifier)
    assert ctl.settings.hotkeys.hide == "Ctrl+F4" and "пропускают" in w.keys.notes["hide"].text()
    add = w.keys.edits["add_game"]
    add.click()
    QTest.keyClick(add, Qt.Key.Key_Escape)
    assert ctl.settings.hotkeys.add_game == ""
    assert ctl.paused[-1] is False  # передумал (Esc): клавиши LexoPlay снова работают
    same = w.keys.edits["translate"]
    same.click()
    QTest.keyClick(same, Qt.Key.Key_F2)  # та же клавиша, что была
    assert ctl.paused[-1] is False
    same.click()
    w.show_page("games")  # ушёл с вкладки посреди ввода
    assert ctl.paused[-1] is False
    # боковая кнопка мыши: Mouse5 на перевод
    w.show_page("keys")
    edit = w.keys.edits["translate"]
    edit.click()
    QTest.mouseClick(edit, Qt.MouseButton.ForwardButton)
    assert ctl.settings.hotkeys.translate == "Mouse5" and ctl.paused[-1] is False
    assert "получит и игра" in w.keys.notes["translate"].text()
    edit.click()
    QTest.mouseClick(edit, Qt.MouseButton.LeftButton)  # левая кнопка: отмена, ничего не меняется
    assert ctl.settings.hotkeys.translate == "Mouse5" and ctl.paused[-1] is False


def test_view_page_saves_sliders(window, qapp):
    w, ctl = window
    w.show_page("view")
    w.view.size.setValue(130)
    assert abs(ctl.settings.font_scale - 1.3) < 1e-9
    w.view._mode("always")  # noqa: SLF001
    assert ctl.settings.magpie_mode == "always"


def test_close_hides_instead_of_quit(window, qapp):
    w, _ctl = window
    w.open("games")
    w.close()
    assert not w.isVisible()


def test_picker_draws_freeze_frame(qapp):
    from PySide6.QtGui import QColor, QImage

    bg = QImage(800, 600, QImage.Format.Format_RGB888)
    bg.fill(QColor(240, 240, 240))
    picker = RegionPicker(Rect(0, 0, 800, 600), "Danganronpa", background=bg)
    picker.open()
    qapp.processEvents()
    img = picker.grab().toImage()
    px = img.pixelColor(400, 500)
    assert px.red() > 120  # светлый кадр под лёгким затемнением, а не чёрный экран
    got = {}
    picker.accepted.connect(lambda r, n, s: got.update(region=r))
    QTest.mousePress(picker, Qt.MouseButton.LeftButton, pos=QPoint(40, 450))
    QTest.mouseMove(picker, QPoint(760, 580))
    QTest.mouseRelease(picker, Qt.MouseButton.LeftButton, pos=QPoint(760, 580))
    QTest.keyClick(picker, Qt.Key.Key_Return)
    assert got["region"].w > 0.8


def test_history_page_sessions_search_star_and_delete(window, qapp):
    import time

    from PySide6.QtWidgets import QLabel

    w, ctl = window
    old = time.time() - 3 * 86400  # позавчерашняя сессия
    ctl.store.line_add("dr1_us.exe", "ru", "long ago", "Kyoko", "That's a contradiction.", "Вот и противоречие.")
    with ctl.store._lock:  # noqa: SLF001
        ctl.store._db.execute("UPDATE lines SET created_at = ?, seen_at = ? WHERE norm = 'long ago'", (old, old))  # noqa: SLF001
        ctl.store._db.commit()  # noqa: SLF001
    ctl.store.line_add("dr1_us.exe", "ru", "hello there", "Makoto", "Hello there!", "Привет!")
    ctl.store.line_add("dr1_us.exe", "ru", "the class trial begins", None, "The class trial begins.", "Классный суд начинается.")
    w.show_page("history")
    page = w.history

    def texts():
        return " | ".join(lbl.text() for c in page.cards for lbl in c.findChildren(QLabel) if lbl.isVisibleTo(c))

    assert len(page.cards) == 2 and page.cards[0].opened and not page.cards[1].opened  # свежая сессия раскрыта
    assert "2 сессии" in page.summary.text() and "3 реплики" in page.summary.text()
    assert "Привет!" in texts() and "Makoto" in texts() and "Вот и противоречие." in texts()  # у свёрнутой видно последнюю
    page.cards[1].toggle()
    assert page.cards[1].opened and "That" in texts()
    # поиск: без регистра и по-русски тоже, с подсветкой
    page.search.setText("СУД")
    page._reset()  # noqa: SLF001  без ожидания таймера поиска
    assert len(page.cards) == 1 and "Нашлось 1 реплика в 1 сессии" == page.summary.text()
    assert "Классный " in texts() and "Привет!" not in texts() and "background-color" in texts()
    # избранное
    line = ctl.store.lines_list("dr1_us.exe", "trial")[0]
    page.star_line(line, True)
    page.search.setText("")
    page.star_btn.setChecked(True)
    assert len(page.cards) == 1 and "В избранном 1 реплика" == page.summary.text()
    page.star_line(ctl.store.lines_list("dr1_us.exe", "trial")[0], False)
    assert not page.cards and "пусто" in " ".join(lbl.text() for lbl in page.findChildren(QLabel))
    page.star_btn.setChecked(False)
    page.delete_line(line.id)
    assert ctl.store.lines_count() == 2 and "2 реплики" in page.summary.text()


def test_history_line_explain_retranslate_and_save(window, qapp):
    from PySide6.QtWidgets import QLabel

    w, ctl = window
    ctl.store.line_add("dr1_us.exe", "ru", "the class trial begins", None, "The class trial begins.", "Классный суд начинается.")
    w.show_page("history")
    page = w.history
    line = ctl.store.lines_list()[0]

    def texts():
        return " | ".join(lbl.text() for c in page.cards for lbl in c.findChildren(QLabel))

    page.toggle_explain(line)
    assert ctl.explained_lines == [line.id] and "классный суд" in texts() and "Торжественно." in texts()
    page.save_phrase(line, 0, "class trial", "классный суд")
    assert ctl.saved_phrases == ["class trial"] and (line.id, 0) in page.saved and "сохранено в «Слова»" in texts()
    page.retranslate(line)
    assert "Новый перевод." in texts() and "Переведено заново" in texts() and not page.busy
    assert line.id not in page.explain_open  # разбор старого перевода свернулся, новый по кнопке
    page.toggle_explain(line)
    assert line.id in page.explain_open and ctl.explained_lines == [line.id, line.id]


def test_session_copy_text():
    from lexoplay.store import StoredLine
    from lexoplay.timeline import split_sessions
    from lexoplay.ui.history_page import rich, session_text

    s = split_sessions([("dr1_us.exe", 1_790_000_000.0, "line")])[0]
    line = StoredLine(1, "dr1_us.exe", "n", "Makoto", "Hello!", "Привет!", "ru", 1.0, 1.0, 0)
    text = session_text("Danganronpa", s, [line])
    assert text.startswith("Danganronpa, ") and text.endswith("Makoto: Hello!\nПривет!")
    assert rich("Ёлка <b>", "елк") == '<span style="background-color:#FBE3A0">Ёлк</span>а &lt;b&gt;'


def test_words_page_and_export(window, qapp, tmp_path):
    from PySide6.QtWidgets import QLabel

    from lexoplay.store import SavedWord

    w, ctl = window
    ctl.store.word_add(
        SavedWord(
            "dr1_us.exe", "Danganronpa", "despair", "despair", "отчаяние", "dɪˈspɛr", "noun",
            "Welcome to despair!", "Добро пожаловать в отчаяние!", created_at=1_790_000_000,
        )
    )
    w.show_page("words")
    texts = [lbl.text() for lbl in w.words.box.findChildren(QLabel)]
    joined = " ".join(texts)
    assert "despair" in joined and "отчаяние" in joined and "только здесь" in texts
    assert "не привязан" in w.words.note.text()
    out = tmp_path / "w.txt"
    assert ctl.export_words(str(out), "anki") == 1
    body = out.read_text(encoding="utf-8")
    assert body.startswith("#separator:tab") and "<b>despair</b>" in body and "LexoPlay::Danganronpa" in body
    csv_out = tmp_path / "w.csv"
    ctl.export_words(str(csv_out), "csv")
    assert "отчаяние" in csv_out.read_text(encoding="utf-8-sig")


def test_more_page_key_check_autostart_update(window, qapp):
    from types import SimpleNamespace

    w, ctl = window
    w.show_page("more")
    page = w.more
    page.key.setText("bad")
    page._check_key()  # noqa: SLF001
    assert "не подходит" in page.key_status.text()
    page.key.setText("sk-ant-123")
    page._check_key()  # noqa: SLF001
    assert "работает" in page.key_status.text()
    assert "реплик: 3" in page.usage.text()
    page.autostart.setChecked(True)
    assert ctl.autostart
    assert not w.update_bar.isVisibleTo(w)
    ctl.update.phase = "available"
    ctl.update.release = SimpleNamespace(version="9.9.9")
    w.refresh_update()
    assert w.update_bar.isVisibleTo(w) and "9.9.9" in w.update_bar.text.text()
    assert page.update_btn.text() == "Обновить"
    w.update_bar.btn.click()
    assert ctl.installs == 1


def test_game_card_shows_last_reading_and_glossary(window, qapp):
    from PIL import Image
    from PySide6.QtWidgets import QLabel

    from lexoplay.ui.pages import GlossaryDialog

    w, ctl = window

    class Read:
        image = Image.new("RGB", (400, 80), "navy")
        text = "Welcome to Hope's Peak Academy!"
        mode_name = "пиксельный шрифт"

    ctl.last_reads["dr1_us.exe"] = Read()
    w.show_page("games")
    texts = [lbl.text() for lbl in w.games.findChildren(QLabel)]
    assert any("Hope's Peak" in t for t in texts) and any("пиксельный шрифт" in t for t in texts)
    ctl.store.line_add("dr1_us.exe", "ru", "x", "Monokuma", "Upupu", "Пупупу")
    dlg = GlossaryDialog(ctl, ctl.store.get_game("dr1_us.exe"))
    assert any(b.text() == "Monokuma" for b in dlg.findChildren(type(w.more.check_btn)))
    dlg.term.setText("Monokuma")
    dlg.tr.setText("Монокума")
    dlg._add()  # noqa: SLF001
    assert ctl.store.glossary("dr1_us.exe") == {"Monokuma": "Монокума"}
    dlg.deleteLater()


def test_picker_moves_and_resets_translation_strip(qapp):
    picker = RegionPicker(Rect(0, 0, 800, 600), "Test Game", region=Rect(0.05, 0.75, 0.9, 0.2))
    picker.open()
    got = {}
    picker.accepted.connect(lambda r, n, s: got.update(region=r, strip=s))
    strip = picker.strip_rect()
    assert strip is not None and strip.bottom() <= 450  # по умолчанию над окном диалога
    start = strip.center().toPoint()
    QTest.mousePress(picker, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(picker, QPoint(start.x(), 60))
    QTest.mouseRelease(picker, Qt.MouseButton.LeftButton, pos=QPoint(start.x(), 60))
    QTest.keyClick(picker, Qt.Key.Key_Return)
    s = got["strip"]
    assert s is not None and s.y < 0.15  # перетащили наверх
    assert abs(got["region"].y - 0.75) < 1e-6  # окно диалога не тронули
    picker2 = RegionPicker(Rect(0, 0, 800, 600), "Test Game", region=Rect(0.05, 0.75, 0.9, 0.2), strip=s)
    picker2.open()
    got2 = {}
    picker2.accepted.connect(lambda r, n, s2: got2.update(strip=s2))
    QTest.keyClick(picker2, Qt.Key.Key_R)
    QTest.keyClick(picker2, Qt.Key.Key_Return)
    assert got2["strip"] is None


def test_progress_page_shows_today_week_and_goal(window, qapp):
    from datetime import date

    w, ctl = window
    today = date.today().isoformat()
    ctl.store.stats_add(today, "dr1_us.exe", lines=12, words=3, seconds=1500)
    w.show_page("progress")
    page = w.progress
    assert page.lines_today.text() == "12"
    assert page.words_today.text() == "3 из 10"
    assert page.time_today.text() == "25 мин" and page.streak.text() == "1"
    assert "реплик 12" in page.week_note.text()
    page._goal(3)  # noqa: SLF001
    assert ctl.settings.daily_goal == 3 and page.words_note.text() == "цель выполнена"
    # переключатели: календарь и неделя по словам, игры за всё время
    page.metric_seg.buttons["words"].click()
    assert page.chart.measure == "words" and page.calendar.measure == "words"
    page.period_seg.buttons["all"].click()
    from PySide6.QtWidgets import QLabel

    games = [lbl.text() for lbl in page.games_box.findChildren(QLabel)]
    assert "Danganronpa" in games
    records = [lbl.text() for lbl in page.records_box.findChildren(QLabel)]
    assert "12 реплик" in records
    page.grab()  # отрисовка календаря, часов и полос без ошибок


def test_hours_peak_and_activity_levels():
    from lexoplay.ui.progress_page import ActivityMap, best_window, hours_note

    hours = [0] * 24
    hours[23], hours[0], hours[1] = 10, 12, 8  # через полночь
    hours[15] = 3
    assert best_window(hours) == (23, 2)
    assert hours_note(hours).startswith("Чаще всего ты играешь ночью, с 23:00 до 2:00")
    assert hours_note([0] * 24).startswith("Покажется")
    th = ActivityMap.thresholds([0, 1, 2, 3, 4, 50, 0])
    m = ActivityMap.__new__(ActivityMap)
    assert m.level_color(0, th) == "#ECEAE4" and m.level_color(50, th) == "#0F6E56"


def test_word_layer_explain_panel_and_clicks_stay_out_of_the_game(qapp):
    from lexoplay.wordmode import ExplainRow, ExplainState, match_phrase

    res = OcrResult((_ocr_line("Oh, and they mentioned the Ultimate Biker Gang Leader too."),))
    views = build_views(res)
    layer = WordLayer()
    clicked, saves, done = [], [], []
    layer.explain_clicked.connect(lambda: clicked.append(1))
    layer.explain_save.connect(saves.append)
    layer.finished.connect(done.append)
    layer.open(Rect(0, 0, 1000, 700), Rect(0.05, 0.8, 0.9, 0.15), views)
    qapp.processEvents()
    layer.grab()  # отрисовка: появилась кнопка «Разбор реплики»
    assert layer._explain_btn is not None  # noqa: SLF001
    b = layer._explain_btn.center()  # noqa: SLF001
    QTest.mouseClick(layer, Qt.MouseButton.LeftButton, pos=QPoint(int(b.x()), int(b.y())))
    assert clicked == [1] and layer.is_open
    rows = [ExplainRow("Ultimate Biker Gang Leader", "Абсолютный главарь банды байкеров", "титул в игре")]
    rows[0].indices = match_phrase(views, rows[0].phrase)
    layer.set_explain(ExplainState("ready", rows, "Спокойный тон."))
    img = layer.grab().toImage()
    assert layer.explain_open and len(layer._explain_saves) == 1 and img.width() == 1000  # noqa: SLF001
    s = layer._explain_saves[0].center()  # noqa: SLF001
    QTest.mouseClick(layer, Qt.MouseButton.LeftButton, pos=QPoint(int(s.x()), int(s.y())))
    assert saves == [0]
    # колесо и клик по пустому месту в игру не уходят: колесо глотаем, клик закрывает режим
    QTest.mouseClick(layer, Qt.MouseButton.LeftButton, pos=QPoint(500, 40))
    assert done == ["outside"] and not layer.is_open
    layer.close()
    layer.deleteLater()
    qapp.processEvents()


def _count(img, pred, rect=None):  # type: ignore[no-untyped-def]
    from PySide6.QtCore import QRect

    r = rect or QRect(0, 0, img.width(), img.height())
    n = 0
    for y in range(max(0, r.top()), min(img.height(), r.bottom()), 2):
        for x in range(max(0, r.left()), min(img.width(), r.right()), 2):
            if pred(img.pixelColor(x, y)):
                n += 1
    return n


def test_overlay_recall_chips_under_translation(qapp):
    from lexoplay.recall import RecallChip

    ov = Overlay()
    ov.fade = False  # снимок сразу, без плавного появления
    ov.set_display_rect(Rect(0, 0, 960, 720))
    region = Rect(0.05, 0.78, 0.9, 0.18)
    ov.show_text(region, "Здесь как-то жутковато.")
    plain = ov.strip_rect()
    chips = [RecallChip("eerie", "жуткий", 1), RecallChip("dread", "страх", 3)]
    ov.show_text(region, "Здесь как-то жутковато, и этот страх.", recall=chips)
    assert ov.recall == tuple(chips)
    tall = ov.strip_rect()
    assert tall.h > plain.h + 10  # строка подсказок под переводом
    box, _f, text_rect, _s, _o, row = ov._strip_geometry(ov._shown())  # noqa: SLF001
    assert row is not None and row.label is not None and len(row.chips) == 2
    assert all(c.rect.top() >= text_rect.bottom() and box.contains(c.rect) for c in row.chips)
    assert row.chips[0].count == "" and row.chips[1].count == "×3"
    img = ov.grab().toImage()
    chip = row.chips[0].rect.toRect()
    yellow = _count(img, lambda c: c.red() > 180 and c.green() > 150 and c.blue() < 120, chip)
    assert yellow > 5  # слово жёлтое, как твои слова в режиме слов
    ov.show_busy(region, original="So eerie.", recall=chips[:1])  # подсказка видна уже с точками
    assert ov._strip_geometry(ov._shown())[5] is not None  # noqa: SLF001
    ov.show_text(region, "Другая реплика.")  # без recall подсказки уходят
    assert ov.recall == ()
    # узкая плашка: длинный перевод обрезается, чипы переносятся и не вылезают
    ov.strip_custom = Rect(0.3, 0.6, 0.22, 0.1)
    ov.show_text(region, "Перевод", recall=[RecallChip("overwhelming", "ошеломляющий, подавляющий весь", 2)] * 3)
    box, _f, _t, _s, _o, row = ov._strip_geometry(ov._shown())  # noqa: SLF001
    assert len({round(c.rect.top()) for c in row.chips}) >= 2
    assert all(c.rect.right() <= box.right() + 0.5 for c in row.chips)
    ov.close()


def test_word_layer_highlights_rare_words_with_legend(qapp):
    from lexoplay.ui.theme import RARE
    from lexoplay.wordmode import reader_views

    layer = WordLayer()
    layer.rare_label = "B2+"
    views = reader_views("Kyoko felt the eerie dread of the school.", lambda w: w == "school.", level="b2", names={"Kyoko"})
    by = {v.text.strip("."): v for v in views}
    assert by["eerie"].tier == "rare" and by["dread"].tier == "rare" and by["Kyoko"].tier == "name"
    assert by["school"].known and by["the"].stop
    layer.open(Rect(0, 0, 1000, 700), Rect(0.05, 0.8, 0.9, 0.15), views)
    qapp.processEvents()
    img = layer.grab().toImage()

    def rose(c):  # type: ignore[no-untyped-def]
        return c.red() > 140 and c.blue() > 70 and c.green() < c.red() - 50

    def word_box(v):  # type: ignore[no-untyped-def]
        from PySide6.QtCore import QRect

        r = layer.views[v.index].rect
        return QRect(int(r.x * img.width()) - 4, int(r.y * img.height()) - 4, int(r.w * img.width()) + 8, int(r.h * img.height()) + 10)

    assert _count(img, rose, word_box(by["eerie"])) > 5
    assert _count(img, rose, word_box(by["Kyoko"])) == 0
    panel = layer.panel
    from PySide6.QtCore import QRect

    above = QRect(0, max(0, int(panel.y()) - 80), img.width(), 80)
    assert _count(img, rose, above) > 0  # «редкие B2+» в подсказке над панелью
    assert RARE.name().lower() == "#ff7a9c"
    layer.close()
    layer.deleteLater()
    qapp.processEvents()


def test_overlay_strip_muted_under_word_mode(qapp):
    ov = Overlay()
    ov.fade = False
    ov.set_display_rect(Rect(0, 0, 960, 720))
    ov.show_text(Rect(0.05, 0.78, 0.9, 0.18), "Перевод")
    assert ov.isVisible()
    ov.mute_strip(True)  # режим слов открыт: плашки нет, окно не спорит с панелью, кто выше
    assert ov.strip_visible and not ov.isVisible() and ov.strip_rect() is not None
    ov.toast("тост")  # тосты по-прежнему видны
    assert ov.isVisible()
    ov._clear_toast()  # noqa: SLF001
    assert not ov.isVisible()
    ov.hide_strip()
    ov.mute_strip(False)
    assert not ov.isVisible()  # убрали, пока была спрятана: не возвращается
    ov.show_text(Rect(0.05, 0.78, 0.9, 0.18), "Ещё")
    ov.mute_strip(True)
    ov.mute_strip(False)
    assert ov.isVisible()
    ov.close()


def test_words_page_search_sort_bold_and_explain(window, qapp):
    from PySide6.QtWidgets import QLabel

    from lexoplay.store import SavedWord

    w, ctl = window
    st = ctl.store
    st.word_add(SavedWord("dr1_us.exe", "Danganronpa", "lured", "lure", "заманивать, приманивать",
                          sentence="But the target had to be lured out.", sentence_translation="Но жертву пришлось выманить.",
                          created_at=1_790_000_000))
    st.word_add(SavedWord("dr1_us.exe", "Danganronpa", "rebuttal", "rebuttal", "опровержение",
                          sentence="So, you have no rebuttal?", created_at=1_790_000_100, seen=0))
    wid = st.word_add(SavedWord("dr1_us.exe", "Danganronpa", "Blameless", "blameless", "невиновный",
                                sentence="She wasn't a blameless victim.", created_at=1_790_000_050))
    st.words_seen([wid], 1_790_000_200)
    w.show_page("words")
    page = w.words

    def texts():  # type: ignore[no-untyped-def]
        return [lbl.text() for lbl in page.box.findChildren(QLabel)]

    assert page.summary.text() == "Слов: 3"
    lured = next(t for t in texts() if "target" in t)
    assert "<b" in lured and ">lured</b>" in lured  # слово в примере жирное
    assert any("Но жертву" in t for t in texts())  # перевод примера под ним
    # поиск по-русски (ё = е, без регистра) и по-английски
    page.search.setText("ЗАМАНИВ")
    QTest.qWait(300)
    assert [x.word for x in page.visible_words()] == ["lure"] and page.summary.text() == "Найдено: 1 из 3"
    page.search.setText("victim")
    QTest.qWait(300)
    assert [x.word for x in page.visible_words()] == ["blameless"]
    assert any("background-color" in t for t in texts())  # найденное подсвечено
    page.search.setText("zzz")
    QTest.qWait(300)
    assert page.visible_words() == [] and "Ничего не нашлось" in page.summary.text()
    page.search.clear()
    QTest.qWait(300)
    # сортировка
    page.sort.setCurrentIndex(page.sort.findData("abc"))
    page.sort.activated.emit(page.sort.currentIndex())
    assert [x.word for x in page.visible_words()] == ["blameless", "lure", "rebuttal"]
    page.sort.setCurrentIndex(page.sort.findData("seen"))
    page.sort.activated.emit(page.sort.currentIndex())
    assert page.visible_words()[0].word == "blameless"
    page.sort.setCurrentIndex(page.sort.findData("new"))
    page.sort.activated.emit(page.sort.currentIndex())
    assert page.visible_words()[0].word == "rebuttal"
    # разбор примера: как в «Истории», пункт разбора сохраняется в слова
    target = next(x for x in page.visible_words() if x.word == "lure")
    page.toggle_explain(target)
    assert ctl.explained_lines and any("Торжественно" in t for t in texts())
    from lexoplay.lookup import ExplainItem

    page.save_phrase(target, 0, ExplainItem("class trial", "классный суд", ""))
    assert any("сохранено" in t for t in texts())


def _wait_for(cond, ms=2000):  # type: ignore[no-untyped-def]
    import time

    end = time.time() + ms / 1000
    while not cond() and time.time() < end:
        QTest.qWait(20)
    return cond()


def test_smooth_scroll_and_long_words_list(window, qapp):
    """Колесо мыши крутит плавно; длинный список слов строится частями по мере прокрутки."""
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QWheelEvent

    from lexoplay.store import SavedWord
    from lexoplay.ui.pages import WORDS_CHUNK

    w, ctl = window
    for i in range(80):
        ctl.store.word_add(SavedWord("dr1_us.exe", "DR", f"word{i}", f"word{i}", "перевод", sentence=f"A word{i} here.", created_at=1_790_000_000 + i))
    w.resize(900, 700)
    w.show()
    w.show_page("words")
    page = w.words
    assert 0 < len(page._rows) < WORDS_CHUNK  # noqa: SLF001  первые строки сразу
    _wait_for(lambda: len(page._rows) == WORDS_CHUNK)  # noqa: SLF001  остальные следом
    assert len(page._rows) == WORDS_CHUNK and page.more.isVisible()  # noqa: SLF001
    sc = page._scroll  # noqa: SLF001
    bar = sc.verticalScrollBar()
    QTest.qWait(50)
    assert bar.maximum() > 500, bar.maximum()
    start = bar.value()
    ev = QWheelEvent(QPointF(100, 100), QPointF(100, 100), QPoint(0, 0), QPoint(0, -120),
                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    qapp.sendEvent(sc.viewport(), ev)
    qapp.processEvents()
    assert bar.value() - start < 60  # не прыжком
    QTest.qWait(350)
    assert bar.value() - start >= 80  # а доехало плавно
    bar.setValue(bar.maximum())  # долистали до конца: следующая часть достраивается по нескольку строк
    _wait_for(lambda: len(page._rows) > WORDS_CHUNK)  # noqa: SLF001
    assert len(page._rows) > WORDS_CHUNK  # noqa: SLF001
    # удаление убирает одну строку, не перестраивая список
    first = page.visible_words()[0]
    other = page._rows[page.visible_words()[1].id]  # noqa: SLF001
    page._delete(first.id)  # noqa: SLF001
    assert first.id not in page._rows and page._rows[page.visible_words()[0].id] is other  # noqa: SLF001
    assert page.summary.text() == "Слов: 79"
    # открыли вкладку снова, ничего не менялось: список не строится заново
    rows = dict(page._rows)  # noqa: SLF001
    w.show_page("games")
    w.show_page("words")
    assert page._rows == rows  # noqa: SLF001


def test_overlay_strip_grows_smoothly(qapp):
    ov = Overlay()
    ov.set_display_rect(Rect(0, 0, 960, 720))
    region = Rect(0.05, 0.78, 0.9, 0.18)
    ov.show_text(region, "Коротко.")
    QTest.qWait(200)  # проявилась
    ov.grab()
    small = ov._box_now().height()  # noqa: SLF001
    ov.show_text(region, "Длинный перевод, который точно переносится на несколько строк. " * 4)
    ov.grab()  # кадр сразу после: плашка ещё не доросла
    target = ov._strip_geometry(ov._shown())[0].height()  # noqa: SLF001
    mid = ov._box_now().height()  # noqa: SLF001
    assert small < target and mid < target
    QTest.qWait(250)
    ov.grab()
    assert abs(ov._box_now().height() - target) < 0.5  # noqa: SLF001
    ov.hide_strip()
    QTest.qWait(250)
    ov.show_text(Rect(0.05, 0.1, 0.9, 0.18), "Новая плашка в другом месте.")
    QTest.qWait(40)
    ov.grab()  # появилась сразу на своём месте, не поехала от прошлой
    assert ov._box_now() == ov._strip_geometry(ov._shown())[0]  # noqa: SLF001
    ov.close()


def test_overlay_streamed_text_reveals_smoothly(qapp):
    ov = Overlay()
    ov.set_display_rect(Rect(0, 0, 960, 720))
    region = Rect(0.05, 0.78, 0.9, 0.18)
    ov.show_text(region, "Сразу целиком.")  # плашки не было: текст виден сразу
    assert ov._reveal == len("Сразу целиком.")  # noqa: SLF001
    ov.hide_strip()
    ov.show_busy(region)
    ov.show_text(region, "Подозреваемые утверждают,")
    assert ov._reveal < len("Подозреваемые утверждают,")  # noqa: SLF001  дописанное проявляется
    ov.show_text(region, "Подозреваемые утверждают, что им угрожали.")
    _wait_for(lambda: ov._reveal >= len("Подозреваемые утверждают, что им угрожали."), 1500)  # noqa: SLF001
    assert ov._reveal == len(ov.text)  # noqa: SLF001
    ov.show_text(region, "Совсем другой перевод.")  # перевели заново: без печатания
    assert ov._reveal == len("Совсем другой перевод.")  # noqa: SLF001
    ov.close()


@pytest.mark.parametrize(
    "size,region,scale",
    [
        ((1707, 1067), Rect(0.0, 0.6348, 1.0, 0.3155), 1.0),  # Danganronpa на 2560×1600 с масштабом 150 %
        ((1280, 720), Rect(0.05, 0.75, 0.9, 0.2), 1.6),
        ((800, 600), Rect(0.0, 0.63, 1.0, 0.32), 1.3),
        ((1707, 1067), Rect(0.0, 0.4, 1.0, 0.3), 1.6),  # окно диалога посередине, места мало и сверху, и снизу
    ],
)
def test_word_card_never_covers_buttons_or_panel(qapp, size, region, scale):
    """Карточка слова налезала на кнопки «Перевести иначе» и «Разбор реплики», подсказка у «+» обрезалась."""
    from PySide6.QtCore import QRectF

    from lexoplay.wordmode import CardState, reader_views

    w, h = size
    layer = WordLayer()
    layer.can_retranslate = True
    layer.hover_pick = True
    layer.deck_label = "Сохранится во вкладку «Слова» в LexoPlay"
    layer.font_scale = scale
    layer.resize(w, h)
    text = "That's why I'm still alive, because I don't know what will happen next. That's why I desperately"
    layer.open(Rect(0, 0, w, h), region, reader_views(text), speaker="Monokuma", translation="Вот почему я ещё жив.")
    qapp.processEvents()
    last = [v.index for v in layer.views if v.text == "desperately"][0]
    layer._sel = [last]  # noqa: SLF001
    layer.set_card(CardState("ready", "desperately", "ˈdɛspərətli", "adv", ["отчаянно", "изо всех сил"], local=True))
    layer.grab()
    card = layer._card_rect  # noqa: SLF001
    assert card is not None and QRectF(0, 0, w, h).contains(card)
    assert not card.intersects(layer.panel.adjusted(1, 1, -1, -1))
    for btn in (layer._explain_btn, layer._retranslate_btn):  # noqa: SLF001
        assert btn is None or not card.intersects(btn)  # кнопка либо в стороне, либо не нарисована под карточкой
    layer.close()


def test_scroll_extras_sticky_search_top_button_keys(window, qapp):
    """Поиск прилипает к верху, кнопка «наверх» появляется и плавно возвращает, клавиши крутят плавно."""
    from lexoplay.store import SavedWord

    w, ctl = window
    for i in range(60):
        ctl.store.word_add(SavedWord("dr1_us.exe", "DR", f"word{i}", f"word{i}", "перевод", sentence=f"A word{i} here.", created_at=1_790_000_000 + i))
    w.resize(900, 650)
    w.show()
    w.show_page("words")
    page = w.words
    sc = page._scroll  # noqa: SLF001
    bar = sc.verticalScrollBar()
    _wait_for(lambda: bar.maximum() > 1500)
    page.search.setFocus()
    page.search.setText("word")  # набранное и фокус переживают прилипание
    QTest.qWait(250)
    _wait_for(lambda: bar.maximum() > 1500)  # после поиска список строится заново, по частям
    assert not sc.stuck
    bar.setValue(900)
    qapp.processEvents()
    assert sc.stuck and page.toolbar.parent() is sc._sticky_host  # noqa: SLF001
    assert page.search.text() == "word" and page.search.hasFocus()
    _wait_for(lambda: sc._top.isVisible())  # noqa: SLF001
    assert sc._top.isVisible()  # noqa: SLF001
    sc._top.click()  # noqa: SLF001
    _wait_for(lambda: bar.value() == 0, 1500)
    qapp.processEvents()
    assert bar.value() == 0 and not sc.stuck and page.toolbar.parent() is not sc._sticky_host  # noqa: SLF001
    assert page.search.hasFocus()
    _wait_for(lambda: not sc._top.isVisible(), 800)  # noqa: SLF001
    # PageDown и End плавно, а не прыжком
    sc.setFocus()
    QTest.keyClick(sc, Qt.Key.Key_PageDown)
    qapp.processEvents()
    assert bar.value() < sc.viewport().height() * 0.85
    _wait_for(lambda: bar.value() >= sc.viewport().height() * 0.8, 800)
    QTest.keyClick(sc, Qt.Key.Key_End)
    _wait_for(lambda: bar.value() == bar.maximum(), 1500)
    assert bar.value() == bar.maximum()
    # Ctrl+F: к поиску текущей вкладки
    w.show_page("history")
    w._focus_search()  # noqa: SLF001
    assert w.history.search.hasFocus()


def test_gc_guard_collects_from_timer(qapp):  # type: ignore[no-untyped-def]
    import gc

    from lexoplay import gcguard

    was = gc.isenabled()
    guard = gcguard.install(qapp)
    try:
        assert not gc.isenabled()  # не внутри обработчика системных сообщений
        assert gcguard.install(qapp) is guard

        class Loop:
            pass

        a = Loop()
        a.me = a  # type: ignore[attr-defined]
        del a
        for _ in range(guard.threshold[0] + 10):
            Loop()
        guard.check()
        assert gc.get_count()[0] <= guard.threshold[0]
    finally:
        guard.timer.stop()
        guard.deleteLater()
        gcguard._installed = None
        if was:
            gc.enable()


def test_game_notes_dialog_shows_progress_and_saves_edit(window, qapp):
    from lexoplay.ui.pages import GameNotesDialog

    _w, ctl = window
    game = ctl.store.get_game("dr1_us.exe")
    dlg = GameNotesDialog(ctl, game)
    assert "пока нет" in dlg.status.text() and dlg.remake.text().strip() == "Собрать"
    dlg._remake()  # noqa: SLF001
    assert ctl.notes_remade == 1 and "Собираю" in dlg.status.text() and dlg.edit.isReadOnly()
    ctl.store.game_notes_set("dr1_us.exe", "ru", "GAME: Danganronpa\nTERMS:\nClass Trial = Классный суд")
    ctl.notes_busy = False
    _wait_for(lambda: not dlg.edit.isReadOnly(), 2000)
    assert "Классный суд" in dlg.edit.toPlainText() and "Собрана моделью" in dlg.status.text()
    dlg.edit.setPlainText(dlg.edit.toPlainText() + "\nFever Time = Фивер-тайм")
    dlg._save()  # noqa: SLF001
    rec = ctl.store.game_notes("dr1_us.exe", "ru")
    assert rec.edited and "Фивер-тайм" in rec.text
    dlg2 = GameNotesDialog(ctl, game)
    assert "Поправлена тобой" in dlg2.status.text()
    dlg.deleteLater()
    dlg2.deleteLater()


def test_page_overlay_covers_each_paragraph_and_peeks(qapp):
    from lexoplay.ui.overlay import Overlay, PageView

    ov = Overlay()
    ov.fade = False
    ov.resize(1280, 720)
    long_ru = "Я кивнул и пошёл за ней через двор, где пахло пылью и старым дождём, а небо было серым и низким, " * 3
    views = [
        PageView(Rect(0.1, 0.12, 0.8, 0.09), 0.045, "Утро выдалось холодным. Я долго стоял у ворот."),
        PageView(Rect(0.1, 0.27, 0.8, 0.045), 0.045, None),  # ещё переводится
        PageView(Rect(0.1, 0.36, 0.8, 0.09), 0.045, long_ru),  # длинный: шрифт мельче, но в пределах до низа
    ]
    ov.show_page(views)
    assert ov.page_visible and not ov.strip_visible
    boxes = ov.page_boxes()
    for (box, text_rect, font), v in zip(boxes, views, strict=True):
        r = QRectF(v.rect.x * 1280, v.rect.y * 720, v.rect.w * 1280, v.rect.h * 720)
        assert box.contains(r)  # английский абзац закрыт целиком
        assert font.pixelSize() >= 12
    assert boxes[0][0].bottom() < boxes[1][0].top()  # абзацы не налезают друг на друга
    assert boxes[2][2].pixelSize() <= boxes[0][2].pixelSize()
    ov.show()
    qapp.processEvents()

    def painted() -> int:
        img = ov.grab().toImage()
        return sum(1 for y in range(0, img.height(), 8) for x in range(0, img.width(), 8) if img.pixelColor(x, y).alpha() > 0)

    assert painted() > 100
    ov.set_peek(True)  # держат Alt: виден оригинал
    assert painted() == 0
    ov.set_peek(False)
    ov.show_busy(Rect(0.05, 0.78, 0.9, 0.18))  # плашка диалога убирает страницу
    assert not ov.page_visible
    ov.hide_strip()
    ov.close()
