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
    picker.accepted.connect(lambda r, n: got.update(region=r, name=n))
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
    assert not ov.isVisible()
    ov.toast("тост")
    assert ov.isVisible()
    ov.close()


# ---------------------------------------------------------------- режим слов и настройки
from lexoplay.ocr_model import Line, OcrResult, Word  # noqa: E402
from lexoplay.ui.settings_dialog import AccountStatus, SettingsDialog  # noqa: E402
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


class FakeAccount:
    def __init__(self) -> None:
        self.linked = False
        self.codes = []

    def account_status(self):
        if self.linked:
            return AccountStatus(True, "@ruslan", "сегодня 3 из 50 переводов")
        return AccountStatus(False, queue="в очереди на отправку: 2")

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


def test_settings_account_link_flow(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("LEXOPLAY_HOME", str(tmp_path))
    from lexoplay.config import Settings
    from lexoplay.store import Store

    store = Store(tmp_path / "local.db")
    acc = FakeAccount()
    dlg = SettingsDialog(Settings(), store, account=acc, tab="account")
    tab = dlg.account
    assert not tab.link_box.isHidden() and tab.linked_box.isHidden()
    assert "очереди" in tab.details.text()
    QTest.keyClicks(tab.code, "badbad")
    assert tab.code.text() == "BADBAD"
    QTest.keyClick(tab.code, Qt.Key.Key_Return)  # Enter привязывает, а не закрывает окно
    assert acc.codes == ["BADBAD"] and "не подошёл" in tab.status.text() and dlg.result() == 0
    tab.code.setText("K7QX2M")
    tab.link_btn.click()
    assert acc.linked and tab.link_box.isHidden() and not tab.linked_box.isHidden()
    assert tab.title.text() == "Lexo: @ruslan"
    tab.unlink_btn.click()
    assert not tab.link_box.isHidden()
    dlg.reject()
    dlg.deleteLater()
    qapp.processEvents()
    store.close()
