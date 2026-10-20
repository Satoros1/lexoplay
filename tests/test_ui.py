"""UI без экрана (QT_QPA_PLATFORM=offscreen): выделение области и оверлей."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
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

    def delete_line(self, line_id):
        self.store.line_delete(line_id)

    def clear_history(self, exe=None):
        self.store.lines_clear(exe)

    def saved_words(self):
        return self.store.words_list()

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


def test_games_page_deck_choice_and_pick(window, qapp):
    w, ctl = window
    ctl.linked = True
    w.lexo.reload_decks()
    w.show_page("games")
    from PySide6.QtWidgets import QComboBox, QPushButton

    combos = w.games.findChildren(QComboBox)
    assert combos, "у привязанного ПК у игры есть выбор колоды"
    combo = combos[0]
    idx = next(i for i in range(combo.count()) if combo.itemData(i) and combo.itemData(i)[0] == "deck")
    combo.activated.emit(idx)
    g = ctl.store.get_game("dr1_us.exe")
    assert g.deck_mode == "deck" and g.deck_id == 5 and g.deck_title == "Слова из игр"
    pick = next(b for b in w.games.findChildren(QPushButton) if "Выделить" in b.text())
    pick.click()
    assert ctl.picked == ["dr1_us.exe"]


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


def test_history_page_lists_searches_and_deletes(window, qapp):
    from PySide6.QtWidgets import QLabel

    w, ctl = window
    ctl.store.line_add("dr1_us.exe", "ru", "hello there", "Makoto", "Hello there!", "Привет!")
    ctl.store.line_add("dr1_us.exe", "ru", "the class trial begins", None, "The class trial begins.", "Классный суд начинается.")
    w.show_page("history")
    texts = [lbl.text() for lbl in w.history.box.findChildren(QLabel)]
    assert "Привет!" in texts and "Классный суд начинается." in texts
    assert any("Makoto" in t for t in texts)
    w.history.search.setText("trial")
    w.history._reset()  # noqa: SLF001  без ожидания таймера поиска
    texts = [lbl.text() for lbl in w.history.box.findChildren(QLabel)]
    assert "Классный суд начинается." in texts and "Привет!" not in texts
    line = ctl.store.lines_list("dr1_us.exe", "trial")[0]
    w.history._delete(line.id)  # noqa: SLF001
    assert ctl.store.lines_count() == 1


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
    assert "despair" in texts and "отчаяние" in texts and "только здесь" in texts
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
