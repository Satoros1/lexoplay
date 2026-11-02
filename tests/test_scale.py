"""Размер подсказок для игры: шаги −/+, растянутые игры (Magpie), хранение и панель слов."""

import os

import pytest

from lexoplay.geometry import UI_SCALES, Rect, game_text_damp, step_scale
from lexoplay.hotkeys import parse
from lexoplay.store import Game, Store


def test_step_scale_moves_by_one_and_stops_at_edges():
    assert step_scale(1.0, 1) == 1.1 and step_scale(1.0, -1) == 0.9
    assert step_scale(UI_SCALES[0], -1) == UI_SCALES[0] and step_scale(UI_SCALES[-1], 1) == UI_SCALES[-1]
    assert step_scale(1.04, 1) == 1.1  # неровное значение: от ближайшего шага


def test_damp_only_for_stretched_games():
    assert game_text_damp(1.0) == 1.0 and game_text_damp(0.8) == 1.0
    assert 1.4 < game_text_damp(2.67) < 1.55  # 800×600 на 1600 по высоте


def test_size_keys_parse():
    assert parse("Minus").vk == 0xBD and parse("Equals").vk == 0xBB
    assert parse("NumPlus").vk == 0x6B and parse("NumMinus").vk == 0x6D


def test_game_scale_saved_and_old_base_upgraded(tmp_path):
    s = Store(tmp_path / "local.db")
    g = s.save_game(Game("ks.exe", "Katawa Shoujo", Rect(0.05, 0.75, 0.9, 0.2)))
    assert s.get_game("ks.exe").ui_scale is None  # авто
    s.save_game(g.with_(ui_scale=0.8))
    assert s.get_game("ks.exe").ui_scale == 0.8
    s.save_game(g.with_(ui_scale=None))
    assert s.get_game("ks.exe").ui_scale is None
    s.close()


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _panel_size(layer, views) -> float:
    layer.open(Rect(0, 0, 1422, 1067), Rect(0.04, 0.72, 0.92, 0.25), views)
    size = layer._plan.size  # noqa: SLF001
    layer.finish("esc")
    return size


def test_stretched_game_gets_smaller_panel_and_size_follows_scale(qapp):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent

    from lexoplay.ui.word_layer import WordLayer
    from lexoplay.wordmode import reader_views

    views = reader_views("The way she doesn't track my movements with her head is telling.")
    layer = WordLayer()
    layer.animate = False
    native = _panel_size(layer, views)
    layer.game_zoom = 2.67  # Katawa Shoujo 800×600 через Magpie на 1600 по высоте
    stretched = _panel_size(layer, views)
    assert stretched < native * 0.8
    layer.font_scale = 0.8
    smaller = _panel_size(layer, views)
    assert smaller < stretched  # размер игры действует и на панель

    steps = []
    layer.zoom_step.connect(steps.append)
    layer.open(Rect(0, 0, 1422, 1067), Rect(0.04, 0.72, 0.92, 0.25), views)
    for dy, mods in ((120, Qt.KeyboardModifier.ControlModifier), (-120, Qt.KeyboardModifier.ControlModifier), (120, Qt.KeyboardModifier.NoModifier)):
        ev = QWheelEvent(QPointF(50, 50), QPointF(50, 50), QPoint(0, 0), QPoint(0, dy), Qt.MouseButton.NoButton, mods, Qt.ScrollPhase.NoScrollPhase, False)
        layer.wheelEvent(ev)
    assert steps == [1, -1]  # без Ctrl колесо просто не пускаем в игру
    layer.notice("Размер для «Katawa Shoujo»: 90%")
    layer.grab()  # надпись рисуется без ошибок
    layer.finish("esc")
    layer.close()


def test_size_buttons_in_word_mode(qapp):
    from PySide6.QtTest import QTest

    from lexoplay.ui.word_layer import HOVER_SIZE_MS, WordLayer
    from lexoplay.wordmode import reader_views

    layer = WordLayer()
    layer.animate = False
    layer.hover_pick = True
    layer.size_label = "авто"
    steps = []
    layer.zoom_step.connect(steps.append)
    layer.open(Rect(0, 0, 1422, 1067), Rect(0.04, 0.72, 0.92, 0.25), reader_views("Don't beat around the bush."))
    layer.grab()  # ряд кнопок нарисован: «− Размер авто +» рядом с «Разбор реплики»
    minus, plus = layer._size_minus, layer._size_plus  # noqa: SLF001
    assert minus is not None and plus is not None and minus.right() < plus.left()
    layer.click(plus.center())
    assert steps == [1]
    layer.hover(minus.center())  # подержать курсор: шаг, и дальше ещё, пока держат
    QTest.qWait(int(HOVER_SIZE_MS * 2.6))
    assert steps[1:] == [-1, -1]
    layer.finish("esc")
    layer.close()


def test_card_stays_on_the_real_window_and_panel(qapp):
    """Qt ещё думает, что окно шире (SetWindowPos он видит с опозданием): карточка у правого края реплики
    всё равно целиком на настоящем окне и в пределах панели."""
    from lexoplay.ui.word_layer import WordLayer
    from lexoplay.wordmode import CardState, reader_views

    layer = WordLayer()
    layer.animate = False
    layer.resize(1600, 1000)
    layer.native_size = lambda _hwnd: (1200.0 * layer.devicePixelRatioF(), 1000.0 * layer.devicePixelRatioF())
    views = reader_views("She stands out and bounces up to extremely rigid attention.")
    layer.open(Rect(0, 0, 1600, 1000), Rect(0.03, 0.72, 0.94, 0.25), views)
    last = len(layer.views) - 2  # «rigid»: у правого края
    layer.select([last])
    layer.set_card(CardState("ready", "rigid", ipa="ˈrɪdʒɪd", pos="adj", senses=["напряжённый", "жёсткий"]))
    layer.grab()
    card = layer._card_rect  # noqa: SLF001
    panel = layer.panel
    assert card is not None and panel is not None
    assert card.right() <= 1200 and panel.right() <= 1200
    assert card.right() <= panel.right() + 5
    layer.finish("esc")
    layer.close()
