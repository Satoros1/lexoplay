"""Счётчик расходов своего ключа Anthropic: сколько реплик и слов сегодня и примерно сколько это стоит.

Без Lexo платит сам игрок, поэтому видно, во что обходится вечер в игре. Токены берём
из ответа модели, цену из таблицы ниже (долларов за миллион токенов). Если модели в
таблице нет, показываем только токены.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import date

from lexoplay.store import Store

# $ за миллион токенов: (вход, выход)
PRICES = {
    "claude-haiku-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
}


def price_for(model: str) -> tuple[float, float] | None:
    for prefix, price in PRICES.items():
        if model.startswith(prefix):
            return price
    return None


@dataclass(frozen=True, slots=True)
class Day:
    day: str
    lines: int = 0
    cached: int = 0  # реплики из кеша: бесплатно
    words: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    usd: float = 0.0  # посчитано по цене той модели, что отвечала (Haiku и Sonnet стоят по-разному)
    priced: bool = False  # хоть один ответ с известной ценой

    def cost(self, model: str) -> float | None:
        if self.priced:
            return self.usd
        price = price_for(model)  # счётчик из 0.4: цены по отдельным моделям не записывались
        if price is None:
            return None
        return self.tokens_in / 1e6 * price[0] + self.tokens_out / 1e6 * price[1]


def money(value: float) -> str:
    if value < 0.01:
        return "меньше цента"
    return f"${value:.2f}"


class OwnUsage:
    KEY = "own_usage"

    def __init__(self, store: Store, today=date.today) -> None:  # type: ignore[no-untyped-def]
        self._store = store
        self._today = today
        self._lock = threading.Lock()

    def _load(self) -> Day:
        day = self._today().isoformat()
        try:
            raw = json.loads(self._store.get_meta(self.KEY) or "{}")
        except json.JSONDecodeError:
            raw = {}
        if raw.get("day") != day:
            return Day(day)
        return Day(
            day,
            int(raw.get("lines", 0)),
            int(raw.get("cached", 0)),
            int(raw.get("words", 0)),
            int(raw.get("in", 0)),
            int(raw.get("out", 0)),
            float(raw.get("usd", 0.0)),
            bool(raw.get("priced", False)),
        )

    def _save(self, d: Day) -> None:
        self._store.set_meta(
            self.KEY,
            json.dumps(
                {
                    "day": d.day,
                    "lines": d.lines,
                    "cached": d.cached,
                    "words": d.words,
                    "in": d.tokens_in,
                    "out": d.tokens_out,
                    "usd": round(d.usd, 6),
                    "priced": d.priced,
                }
            ),
        )

    def add(self, kind: str, tokens: tuple[int, int] | None = None, model: str | None = None) -> Day:
        with self._lock:
            d = self._load()
            t_in, t_out = tokens or (0, 0)
            price = price_for(model) if model else None
            usd = t_in / 1e6 * price[0] + t_out / 1e6 * price[1] if price else 0.0
            d = Day(
                d.day,
                d.lines + (kind == "line"),
                d.cached + (kind == "cached"),
                d.words + (kind == "word"),
                d.tokens_in + t_in,
                d.tokens_out + t_out,
                d.usd + usd,
                d.priced or price is not None,
            )
            self._save(d)
            return d

    def today(self) -> Day:
        with self._lock:
            return self._load()

    def summary(self, model: str) -> str:
        d = self.today()
        if not (d.lines or d.words or d.cached):
            return "Сегодня своим ключом ещё ничего не переводилось."
        parts = [f"реплик: {d.lines}"]
        if d.cached:
            parts.append(f"из кеша бесплатно: {d.cached}")
        if d.words:
            parts.append(f"слов: {d.words}")
        cost = d.cost(model)
        tokens = d.tokens_in + d.tokens_out
        tail = f"примерно {money(cost)} ({tokens:,} токенов)".replace(",", " ") if cost is not None else f"{tokens:,} токенов".replace(",", " ")
        return "Сегодня " + ", ".join(parts) + f". Это {tail}."
