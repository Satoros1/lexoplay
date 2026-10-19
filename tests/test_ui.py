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
