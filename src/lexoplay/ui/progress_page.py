"""Вкладка «Прогресс»: сколько ты читаешь и учишь в играх.

Сверху сегодняшний день и цель. Дальше картина целиком: последние 7 дней столбиками и
календарь за год (каждый день квадратик, чем зеленее, тем больше), когда ты обычно
играешь, рекорды, игры за неделю, месяц или всё время (с тем, кто в игре говорит
больше всех, и сколько новых слов выходит за час), что за слова ты сохраняешь и сколько
английского текста уже прочитал.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPainterPath, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from lexoplay.progress import DayStat, GameStat, Progress, games, minutes
from lexoplay.timeline import MONTHS_GEN, PlaySession, count
from lexoplay.ui import theme
from lexoplay.ui.theme import icon
from lexoplay.ui.widgets import Tile, card, card_list, clear, label, page_shell, setting_row

MONTHS = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
WEEKDAYS_FULL = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
EMPTY = "#ECEAE4"
LEVELS = ["#CFE8DE", "#9DD1BD", "#4FA788", theme.GREEN]  # календарь: от «чуть-чуть» до «много»
POS_COLORS = {
    "noun": "#0F6E56",
    "verb": "#3D5A80",
    "adj": "#C07A2C",
    "adv": "#7A4E7E",
    "phrase": "#2F9C83",
    "other": "#A9A69D",
}
METRICS = [("lines", "Реплики"), ("words", "Слова"), ("seconds", "Время")]
PERIODS = [("week", "Неделя"), ("month", "Месяц"), ("all", "Всё время")]


def day_name(d: date) -> str:
    return f"{d.day} {MONTHS_GEN[d.month - 1]}" + ("" if d.year == date.today().year else f" {d.year}")


def metric_text(metric: str, v: int) -> str:
    if metric == "seconds":
        return minutes(v)
    if metric == "words":
        return count(v, "слово", "слова", "слов")
    return count(v, "реплика", "реплики", "реплик")


def day_tooltip(d: DayStat) -> str:
    head = f"{WEEKDAYS_FULL[d.day.weekday()]}, {day_name(d.day)}"
    if not (d.lines or d.words or d.seconds):
        return f"{head}: ничего"
    return f"{head}: реплик {d.lines}, новых слов {d.words}, в игре {minutes(d.seconds)}"


class Segments(QFrame):
    """Переключатель в одну строку: «Реплики | Слова | Время»."""

    def __init__(self, items: list[tuple[str, str]], on_change: Callable[[str], None]) -> None:
        super().__init__()
        self.setObjectName("segments")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        for key, text in items:
            b = QPushButton(text)
            b.setObjectName("segment")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c=False, k=key: on_change(k))
            self._group.addButton(b)
            self.buttons[key] = b
            lay.addWidget(b)
        self.buttons[items[0][0]].setChecked(True)

    @property
    def value(self) -> str:
        return next(k for k, b in self.buttons.items() if b.isChecked())


def section(title: str, control: QWidget | None = None) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(8)
    row.addWidget(label(title, "section"), 0, Qt.AlignmentFlag.AlignVCenter)
    row.addStretch(1)
    if control is not None:
        row.addWidget(control)
    return row


# ------------------------------------------------------------------ графики
class WeekChart(QWidget):
    """Последние 7 дней столбиками: подпись у сегодняшнего, остальное в подсказке."""

    def __init__(self) -> None:
        super().__init__()
        self.days: list[DayStat] = []
        self.measure = "lines"
        self.setMinimumHeight(170)
        self.setMouseTracking(True)
        self._cols: list[tuple[QRectF, DayStat]] = []

    def set_days(self, days: list[DayStat], metric: str = "lines") -> None:
        self.days, self.measure = days, metric
        self.update()

    def paintEvent(self, _e: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = float(self.width()), float(self.height())
        top, base = 22.0, h - 26.0
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.drawLine(QPointF(0, base + 0.5), QPointF(w, base + 0.5))
        self._cols = []
        if not self.days:
            p.end()
            return
        values = [d.value(self.measure) for d in self.days]
        peak = max(1, max(values))
        slot = w / len(self.days)
        bar_w = min(26.0, slot * 0.5)
        f = theme.ui_font(8.5)
        fb = theme.ui_font(8.5, QFont.Weight.DemiBold)
        for i, d in enumerate(self.days):
            v = values[i]
            cx = slot * i + slot / 2
            today = i == len(self.days) - 1
            hgt = max(4.0, (base - top) * v / peak) if v else 0.0
            if hgt:
                r = QRectF(cx - bar_w / 2, base - hgt, bar_w, hgt)
                path = QPainterPath()
                rad = min(4.0, hgt / 2, bar_w / 2)
                path.moveTo(r.left(), r.bottom())
                path.lineTo(r.left(), r.top() + rad)
                path.quadTo(r.left(), r.top(), r.left() + rad, r.top())
                path.lineTo(r.right() - rad, r.top())
                path.quadTo(r.right(), r.top(), r.right(), r.top() + rad)
                path.lineTo(r.right(), r.bottom())
                path.closeSubpath()
                p.fillPath(path, QColor(theme.GREEN if today else "#4FA788"))
            self._cols.append((QRectF(slot * i, top - 18, slot, base - top + 44), d))
            p.setFont(fb if today else f)
            p.setPen(QColor(theme.INK if today else theme.MUTED))
            p.drawText(QRectF(slot * i, base + 6, slot, 18), int(Qt.AlignmentFlag.AlignHCenter), "сегодня" if today else d.label)
            if v and (today or v == peak):
                p.setFont(fb)
                p.setPen(QColor(theme.INK))
                text = minutes(v) if self.measure == "seconds" else str(v)
                p.drawText(QRectF(cx - 50, base - hgt - 18, 100, 16), int(Qt.AlignmentFlag.AlignHCenter), text)
        p.end()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        for hit, d in self._cols:
            if hit.contains(e.position()):
                QToolTip.showText(e.globalPosition().toPoint(), day_tooltip(d), self)
                return
        QToolTip.hideText()


class ActivityMap(QWidget):
    """Календарь чтения: неделя в столбик (пн сверху), чем зеленее день, тем больше прочитано.

    Сколько недель влезает по ширине, столько и рисуем (до года)."""

    CELL, GAP, LEFT, TOP, LEGEND = 13.0, 3.0, 26.0, 18.0, 26.0

    def __init__(self) -> None:
        super().__init__()
        self.days: list[DayStat] = []
        self.measure = "lines"
        self.setMinimumHeight(int(self.TOP + 7 * (self.CELL + self.GAP) + self.LEGEND))
        self.setMouseTracking(True)
        self._cells: list[tuple[QRectF, DayStat]] = []
        self.active_note = ""

    def set_days(self, days: list[DayStat], metric: str = "lines") -> None:
        self.days, self.measure = days, metric
        self.update()

    def weeks(self) -> int:
        n = int((self.width() - self.LEFT + self.GAP) // (self.CELL + self.GAP))
        return max(4, min(53, n))

    @staticmethod
    def thresholds(values: list[int]) -> list[int]:
        """Границы уровней по четвертям ненулевых дней: цвет честный и при 5, и при 500 репликах в день."""
        vals = sorted(v for v in values if v > 0)
        if not vals:
            return [0, 0, 0]
        return [vals[min(len(vals) - 1, int(len(vals) * q))] for q in (0.25, 0.5, 0.75)]

    def level_color(self, v: int, th: list[int]) -> str:
        if v <= 0:
            return EMPTY
        lvl = sum(1 for t in th if v > t)
        return LEVELS[min(lvl, 3)]

    def paintEvent(self, _e: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._cells = []
        if not self.days:
            p.end()
            return
        today = self.days[-1].day
        n = self.weeks()
        start = today - timedelta(days=today.weekday() + 7 * (n - 1))
        by_day = {d.day: d for d in self.days}
        shown = [by_day.get(start + timedelta(days=i), DayStat(start + timedelta(days=i))) for i in range((today - start).days + 1)]
        th = self.thresholds([d.value(self.measure) for d in shown])
        step = self.CELL + self.GAP
        small = theme.ui_font(8)
        p.setFont(small)
        p.setPen(QColor(theme.MUTED))
        for r, name in ((0, "Пн"), (2, "Ср"), (4, "Пт")):
            p.drawText(QRectF(0, self.TOP + r * step - 2, self.LEFT - 4, self.CELL + 4), int(Qt.AlignmentFlag.AlignVCenter), name)
        prev_month = None
        for w in range(n):
            monday = start + timedelta(days=7 * w)
            if monday.month != prev_month and (w < n - 2 or prev_month is None):
                if prev_month is not None or monday.day <= 7:
                    p.setPen(QColor(theme.MUTED))
                    p.drawText(QRectF(self.LEFT + w * step, 0, 40, self.TOP - 4), int(Qt.AlignmentFlag.AlignBottom), MONTHS[monday.month - 1])
                prev_month = monday.month
            for r in range(7):
                d = start + timedelta(days=7 * w + r)
                if d > today:
                    break
                stat = by_day.get(d, DayStat(d))
                rect = QRectF(self.LEFT + w * step, self.TOP + r * step, self.CELL, self.CELL)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(self.level_color(stat.value(self.measure), th)))
                p.drawRoundedRect(rect, 3, 3)
                if d == today:
                    p.setPen(QPen(QColor(theme.INK), 1.2))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRoundedRect(rect.adjusted(-1, -1, 1, 1), 3.5, 3.5)
                self._cells.append((rect, stat))
        # легенда: слева сколько дней с чтением, справа «меньше ▢▢▢▢▢ больше»
        y = self.TOP + 7 * step + 6
        active = sum(1 for d in shown if d.value(self.measure) > 0)
        p.setPen(QColor(theme.MUTED))
        p.drawText(QRectF(self.LEFT, y, 320, 16), int(Qt.AlignmentFlag.AlignVCenter), self._active_text(active, len(shown)))
        right = self.LEFT + n * step - self.GAP
        more_w = p.fontMetrics().horizontalAdvance("больше")
        p.drawText(QRectF(right - more_w, y, more_w + 2, 16), int(Qt.AlignmentFlag.AlignVCenter), "больше")
        x = right - more_w - 6 - 5 * 14 + 3
        for i, c in enumerate([EMPTY, *LEVELS]):
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(c))
            p.drawRoundedRect(QRectF(x + i * 14, y + 2.5, 11, 11), 2.5, 2.5)
        p.setPen(QColor(theme.MUTED))
        p.drawText(QRectF(x - 66, y, 60, 16), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight), "меньше")
        p.end()

    @staticmethod
    def _active_text(active: int, total: int) -> str:
        months = max(1, round(total / 30.4))
        period = "за месяц" if months == 1 else f"за {count(months, 'месяц', 'месяца', 'месяцев')}"
        return f"{count(active, 'день', 'дня', 'дней')} с чтением {period}"

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        for rect, d in self._cells:
            if rect.adjusted(-1.5, -1.5, 1.5, 1.5).contains(e.position()):
                QToolTip.showText(e.globalPosition().toPoint(), day_tooltip(d), self)
                return
        QToolTip.hideText()


class HoursChart(QWidget):
    """Когда ты играешь: реплики по часам суток, самые частые три часа подряд ярче."""

    def __init__(self) -> None:
        super().__init__()
        self.hours = [0] * 24
        self.peak: tuple[int, int] | None = None  # начало и конец (не включая) лучших трёх часов
        self.setMinimumHeight(120)
        self.setMouseTracking(True)
        self._cols: list[tuple[QRectF, int]] = []

    def set_hours(self, hours: list[int]) -> None:
        self.hours = hours
        self.peak = best_window(hours)
        self.update()

    def paintEvent(self, _e: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = float(self.width()), float(self.height())
        top, base = 8.0, h - 22.0
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.drawLine(QPointF(0, base + 0.5), QPointF(w, base + 0.5))
        self._cols = []
        peak = max(1, max(self.hours))
        slot = w / 24
        bar_w = max(4.0, slot * 0.62)
        hot = set()
        if self.peak:
            a, b = self.peak
            hot = {(a + i) % 24 for i in range((b - a) % 24 or 24)}
        p.setFont(theme.ui_font(8))
        for hr, v in enumerate(self.hours):
            cx = slot * hr + slot / 2
            if v:
                hgt = max(3.0, (base - top) * v / peak)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(theme.GREEN if hr in hot else "#B7DCCD"))
                p.drawRoundedRect(QRectF(cx - bar_w / 2, base - hgt, bar_w, hgt), 2, 2)
            self._cols.append((QRectF(slot * hr, top, slot, base - top + 20), hr))
            if hr % 6 == 0:
                p.setPen(QColor(theme.MUTED))
                p.drawText(QRectF(slot * hr, base + 4, 40, 16), int(Qt.AlignmentFlag.AlignLeft), f"{hr}:00")
        p.end()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        for hit, hr in self._cols:
            if hit.contains(e.position()):
                n = self.hours[hr]
                QToolTip.showText(e.globalPosition().toPoint(), f"{hr}:00–{(hr + 1) % 24}:00: {count(n, 'реплика', 'реплики', 'реплик')}", self)
                return
        QToolTip.hideText()


def best_window(hours: list[int], size: int = 3) -> tuple[int, int] | None:
    """Самые частые size часов подряд (через полночь тоже): (с, до)."""
    if sum(hours) == 0:
        return None
    best = max(range(24), key=lambda s: (sum(hours[(s + i) % 24] for i in range(size)), -s))
    return best, (best + size) % 24


def hours_note(hours: list[int]) -> str:
    win = best_window(hours)
    total = sum(hours)
    if win is None or total < 10:
        return "Покажется, когда переведёшь хотя бы десяток реплик."
    a, b = win
    share = round(100 * sum(hours[(a + i) % 24] for i in range(3)) / total)
    when = "ночью" if a < 5 or a >= 23 else "утром" if a < 11 else "днём" if a < 17 else "вечером"
    return f"Чаще всего ты играешь {when}, с {a}:00 до {b or 24}:00: здесь {share}% всех реплик."


class PosBar(QWidget):
    """Что ты сохраняешь: одна полоса, разделённая по частям речи."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[tuple[str, str, int]] = []
        self.setFixedHeight(14)

    def set_parts(self, parts: list[tuple[str, str, int]]) -> None:
        self.parts = parts
        self.update()

    def paintEvent(self, _e: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        total = sum(n for _k, _name, n in self.parts)
        w, h = float(self.width()), float(self.height())
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
        p.setClipPath(clip)
        p.fillRect(QRectF(0, 0, w, h), QColor(EMPTY))
        x = 0.0
        for i, (key, _name, n) in enumerate(self.parts):
            seg = w * n / total if total else 0.0
            p.fillRect(QRectF(x, 0, seg + (0 if i == len(self.parts) - 1 else 0.5), h), QColor(POS_COLORS.get(key, "#A9A69D")))
            x += seg
        p.end()


class Dot(QWidget):
    def __init__(self, color: str) -> None:
        super().__init__()
        self._color = color
        self.setFixedSize(10, 10)

    def paintEvent(self, _e: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self._color))
        p.drawEllipse(QRectF(0, 0, 10, 10))
        p.end()


# ------------------------------------------------------------------ страница
def stat_tile(caption: str) -> tuple[QFrame, QLabel, QLabel]:
    frame, lay = card()
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(2)
    value = label("0", "statValue")
    lay.addWidget(value)
    lay.addWidget(label(caption, "muted"))
    extra = label("", "version", wrap=True)
    lay.addWidget(extra)
    return frame, value, extra


class ProgressPage(QWidget):
    def __init__(self, ctl) -> None:  # type: ignore[no-untyped-def]
        super().__init__()
        self.ctl = ctl
        self.pr: Progress | None = None
        scroll, lay, _actions = page_shell("Прогресс", "Сколько ты читаешь и учишь в играх: сегодня, за неделю, за год и всего.")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        tiles = QHBoxLayout()
        tiles.setSpacing(10)
        t1, self.lines_today, self.lines_note = stat_tile("Реплик сегодня")
        t2, self.words_today, self.words_note = stat_tile("Новых слов сегодня")
        t3, self.time_today, self.time_note = stat_tile("В игре сегодня")
        t4, self.streak, self.streak_note = stat_tile("Дней подряд")
        for t in (t1, t2, t3, t4):
            tiles.addWidget(t, 1)
        lay.addLayout(tiles)
        self.goal_bar = QProgressBar()
        self.goal_bar.setTextVisible(False)
        t2.layout().addWidget(self.goal_bar)

        # сколько английского уже прочитано
        self.read_box, rl = card()
        rl.setContentsMargins(16, 12, 16, 12)
        row = QHBoxLayout()
        row.setSpacing(12)
        book = QLabel()
        book.setPixmap(icon("words", theme.GREEN, 22).pixmap(22, 22))
        row.addWidget(book, 0, Qt.AlignmentFlag.AlignVCenter)
        self.read_text = label("", None, wrap=True)
        self.read_text.setTextFormat(Qt.TextFormat.RichText)
        row.addWidget(self.read_text, 1)
        rl.addLayout(row)
        lay.addWidget(self.read_box)

        # учёба: сколько читал сам, без перевода
        self.study_box, sl = card()
        sl.setContentsMargins(16, 12, 16, 12)
        srow = QHBoxLayout()
        srow.setSpacing(12)
        cap = QLabel()
        cap.setPixmap(icon("study", theme.GREEN, 22).pixmap(22, 22))
        srow.addWidget(cap, 0, Qt.AlignmentFlag.AlignVCenter)
        self.study_text = label("", None, wrap=True)
        self.study_text.setTextFormat(Qt.TextFormat.RichText)
        srow.addWidget(self.study_text, 1)
        sl.addLayout(srow)
        lay.addWidget(self.study_box)

        lay.addWidget(label("ЦЕЛЬ НА ДЕНЬ", "section"))
        goal_box, gl = card_list()
        self.goal = QComboBox()
        current = int(getattr(ctl.settings, "daily_goal", 10))
        for n in sorted({0, 5, 10, 15, 20, 30, 50, current}):
            self.goal.addItem("Без цели" if n == 0 else f"{n} слов", n)
        self.goal.setCurrentIndex(max(0, self.goal.findData(current)))
        self.goal.setMinimumWidth(150)
        self.goal.activated.connect(lambda _i: self._goal(int(self.goal.currentData())))
        gl.addWidget(
            setting_row(
                "Новых слов в день",
                "Слова, сохранённые в игре клавишами F3 и F7. Когда цель выполнена, LexoPlay скажет",
                self.goal,
                last=True,
            )
        )
        lay.addWidget(goal_box)

        self.metric_seg = Segments(METRICS, lambda _k: self._show_activity())
        lay.addLayout(section("АКТИВНОСТЬ", self.metric_seg))
        act_box, al = card()
        al.addWidget(label("Последние 7 дней", "title"))
        self.chart = WeekChart()
        al.addWidget(self.chart)
        self.week_note = label("", "muted", wrap=True)
        al.addWidget(self.week_note)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {theme.BORDER};")
        al.addWidget(line)
        al.addWidget(label("Календарь", "title"))
        self.calendar = ActivityMap()
        al.addWidget(self.calendar)
        lay.addWidget(act_box)

        lay.addLayout(section("КОГДА ТЫ ИГРАЕШЬ"))
        hours_box, hl = card()
        self.hours = HoursChart()
        hl.addWidget(self.hours)
        self.hours_note = label("", "muted", wrap=True)
        hl.addWidget(self.hours_note)
        lay.addWidget(hours_box)

        lay.addLayout(section("РЕКОРДЫ"))
        self.records_box, self.records_lay = card_list()
        lay.addWidget(self.records_box)

        self.period_seg = Segments(PERIODS, lambda _k: self._show_games())
        lay.addLayout(section("ИГРЫ", self.period_seg))
        self.games_box, self.games_lay = card_list()
        lay.addWidget(self.games_box)

        self.pos_title = section("ЧТО ТЫ СОХРАНЯЕШЬ")
        self.pos_title.setContentsMargins(0, 0, 0, 0)
        self.pos_head = QWidget()
        self.pos_head.setLayout(self.pos_title)
        lay.addWidget(self.pos_head)
        self.pos_box, pl = card()
        self.pos_bar = PosBar()
        pl.addWidget(self.pos_bar)
        self.pos_grid = QGridLayout()
        self.pos_grid.setHorizontalSpacing(24)
        self.pos_grid.setVerticalSpacing(6)
        pl.addLayout(self.pos_grid)
        lay.addWidget(self.pos_box)

        self.totals = label("", "muted", wrap=True)
        lay.addWidget(self.totals)
        lay.addStretch(1)

    def _goal(self, value: int) -> None:
        self.ctl.settings.daily_goal = value
        self.ctl.settings_changed()
        self.refresh()

    def _titles(self) -> dict[str, str]:
        return {g.exe: g.title for g in self.ctl.store.list_games()}

    def refresh(self) -> None:
        pr = self.ctl.progress()
        self.pr = pr
        t = pr.today
        self.lines_today.setText(str(t.lines))
        self.words_today.setText(f"{t.words} из {pr.goal}" if pr.goal else str(t.words))
        self.goal_bar.setVisible(pr.goal > 0)
        self.goal_bar.setRange(0, max(1, pr.goal))
        self.goal_bar.setValue(min(t.words, pr.goal))
        self.words_note.setText("цель выполнена" if pr.goal_done else "")
        self.time_today.setText(minutes(t.seconds))
        self.streak.setText(str(pr.streak))
        best = pr.records.best_streak
        self.streak_note.setText(f"рекорд: {best}" if best > pr.streak else ("это рекорд" if best and best == pr.streak else ""))
        self.read_box.setVisible(pr.words_read > 0)
        pages = pr.pages_read
        book = f", это примерно {count(pages, 'страница', 'страницы', 'страниц')} книги" if pages else ""
        num = f"{pr.words_read:,}".replace(",", "\u00a0")
        self.read_text.setText(f"Прочитано по-английски: <b>{num}</b> {plural_words(pr.words_read)}{book}.")
        self._show_study(pr)
        self._show_activity()
        self.hours.set_hours(pr.hours)
        self.hours_note.setText(hours_note(pr.hours))
        self._show_records()
        self._show_games()
        self._show_pos()
        lines, words, seconds = pr.totals
        self.totals.setText(f"Всего с LexoPlay: реплик {lines}, новых слов {words}, в играх {minutes(seconds)}.")

    def _show_study(self, pr: Progress) -> None:
        lines, peeks, learned, cards = pr.study_today
        week_lines = pr.study_week[0]
        self.study_box.setVisible(bool(week_lines or pr.study_known or getattr(self.ctl.settings, "study_mode", False)))
        parts: list[str] = []
        if lines:
            share = round(100 * (lines - peeks) / lines)
            parts.append(f"Учёба сегодня: сам по-английски <b>{count(lines, 'реплика', 'реплики', 'реплик')}</b>, без перевода {share}%.")
        if week_lines and pr.study_share is not None:
            prev = pr.study_prev_share
            trend = f" (неделей раньше {prev}%)" if prev is not None else ""
            parts.append(f"За неделю без перевода <b>{pr.study_share}%</b>{trend}.")
        if not parts:
            hk = getattr(getattr(self.ctl.settings, "hotkeys", None), "study", "") or "F8"
            parts.append(f"Учёба: в игре нажми {hk}, и реплики читаешь сам, а трудные места подсказаны над окном диалога.")
        if learned or pr.study_known:
            parts.append(f"Запомнилось слов и выражений: <b>{learned}</b> сегодня, {pr.study_known} всего.")
        if cards:
            parts.append(f"Забытые ушли в карточки: {cards}.")
        self.study_text.setText(" ".join(parts))

    def _show_activity(self) -> None:
        pr = self.pr
        if pr is None:
            return
        m = self.metric_seg.value
        self.chart.set_days(pr.week, m)
        self.calendar.set_days(pr.activity, m)
        week_lines = sum(d.lines for d in pr.week)
        week_words = sum(d.words for d in pr.week)
        week_time = sum(d.seconds for d in pr.week)
        self.week_note.setText(f"За 7 дней: реплик {week_lines}, новых слов {week_words}, в игре {minutes(week_time)}")

    def _show_records(self) -> None:
        pr = self.pr
        assert pr is not None
        rec = pr.records
        titles = self._titles()
        rows: list[tuple[str, str, str]] = []

        def sess(s: PlaySession) -> str:
            return f"{titles.get(s.exe, s.exe)}, {day_name(s.day)}"

        if rec.best_lines:
            rows.append(("Больше всего реплик за день", count(rec.best_lines[1], "реплика", "реплики", "реплик"), day_name(rec.best_lines[0])))
        if rec.best_words:
            rows.append(("Больше всего новых слов за день", count(rec.best_words[1], "слово", "слова", "слов"), day_name(rec.best_words[0])))
        if rec.best_streak > 1:
            rows.append(("Самая длинная серия", count(rec.best_streak, "день подряд", "дня подряд", "дней подряд"), f"сейчас: {pr.streak}"))
        if rec.longest and rec.longest.seconds >= 60:
            rows.append(("Самая долгая сессия", minutes(rec.longest.seconds), sess(rec.longest)))
        if rec.biggest:
            rows.append(("Самая насыщенная сессия", count(rec.biggest.lines, "реплика", "реплики", "реплик"), sess(rec.biggest)))
        clear(self.records_lay)
        if not rows:
            self.records_lay.addWidget(empty_row("Рекорды появятся, когда поиграешь с LexoPlay хотя бы день."))
        for i, (title, value, sub) in enumerate(rows):
            row = QFrame()
            row.setObjectName("rowLast" if i == len(rows) - 1 else "row")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(18, 10, 18, 10)
            rl.addWidget(label(title, "muted"), 1)
            col = QVBoxLayout()
            col.setSpacing(0)
            v = label(value, "recordValue")
            v.setAlignment(Qt.AlignmentFlag.AlignRight)
            s = label(sub, "version")
            s.setAlignment(Qt.AlignmentFlag.AlignRight)
            col.addWidget(v)
            col.addWidget(s)
            rl.addLayout(col)
            self.records_lay.addWidget(row)

    def _show_games(self) -> None:
        period = self.period_seg.value
        today = date.today()
        since = {"week": today - timedelta(days=6), "month": today - timedelta(days=29), "all": None}[period]
        items: list[GameStat] = games(self.ctl.store, since)
        titles = self._titles()
        clear(self.games_lay)
        if not items:
            when = {"week": "На этой неделе", "month": "За месяц"}.get(period, "Пока")
            self.games_lay.addWidget(empty_row(f"{when} игр с LexoPlay не было."))
        for i, g in enumerate(items):
            title = titles.get(g.exe, g.exe)
            row = QFrame()
            row.setObjectName("rowLast" if i == len(items) - 1 else "row")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(16, 10, 18, 10)
            rl.setSpacing(12)
            rl.addWidget(Tile(title, g.exe, 34), 0, Qt.AlignmentFlag.AlignVCenter)
            col = QVBoxLayout()
            col.setSpacing(1)
            col.addWidget(label(title, "title"))
            if g.top_speaker:
                col.addWidget(label(f"Чаще всех говорит: {g.top_speaker}", "version"))
            rl.addLayout(col, 1)
            right = QVBoxLayout()
            right.setSpacing(1)
            nums = label(f"реплик {g.lines} · слов {g.words} · {minutes(g.seconds)}", "muted")
            nums.setAlignment(Qt.AlignmentFlag.AlignRight)
            right.addWidget(nums)
            wph = g.words_per_hour
            if wph is not None:
                rate = label(f"{wph:.1f}".replace(".", ",") + " новых слова в час", "version")
                rate.setAlignment(Qt.AlignmentFlag.AlignRight)
                right.addWidget(rate)
            rl.addLayout(right)
            self.games_lay.addWidget(row)

    def _show_pos(self) -> None:
        pr = self.pr
        assert pr is not None
        parts = pr.pos
        self.pos_head.setVisible(bool(parts))
        self.pos_box.setVisible(bool(parts))
        clear(self.pos_grid)
        if not parts:
            return
        self.pos_bar.set_parts(parts)
        total = sum(n for _k, _name, n in parts)
        for i, (key, name, n) in enumerate(parts):
            cell = QHBoxLayout()
            cell.setSpacing(8)
            cell.addWidget(Dot(POS_COLORS.get(key, "#A9A69D")), 0, Qt.AlignmentFlag.AlignVCenter)
            cell.addWidget(label(name.capitalize(), None))
            cell.addStretch(1)
            pct = round(100 * n / total)
            cell.addWidget(label(f"{n} · {pct}%", "muted"))
            box = QWidget()
            box.setLayout(cell)
            self.pos_grid.addWidget(box, i // 2, i % 2)


def plural_words(n: int) -> str:
    from lexoplay.timeline import plural

    return plural(n, "слово", "слова", "слов")


def empty_row(text: str) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(18, 14, 18, 14)
    lay.addWidget(label(text, "muted", wrap=True))
    return w

