"""Экспорт сохранённых слов: для Anki (текстовый файл с табуляцией) и CSV для Excel.

Anki: «Файл → Импорт», поля: слово, перевод с примером из игры, тег с названием игры.
Заголовки #separator, #html и #tags column Anki понимает сам (версии 2.1.55+).
"""

from __future__ import annotations

import csv
import html
import re
from pathlib import Path

from lexoplay.store import SavedWord


def _tag(game: str) -> str:
    tag = re.sub(r"[^\w]+", "_", game.strip()).strip("_")
    return f"LexoPlay::{tag}" if tag else "LexoPlay"


def _flat(text: str | None) -> str:
    return " ".join((text or "").split())


def anki_lines(words: list[SavedWord]) -> list[str]:
    out = ["#separator:tab", "#html:true", "#tags column:3"]
    for w in reversed(words):  # от старых к новым: в Anki так привычнее
        front = html.escape(_flat(w.word))
        if w.ipa:
            front += f" <span style='color:#888'>/{html.escape(_flat(w.ipa))}/</span>"
        back = html.escape(_flat(w.translation)) if w.translation else ""
        if w.sentence:
            example = html.escape(_flat(w.sentence))
            sel = html.escape(_flat(w.selection))
            if sel and sel in example:
                example = example.replace(sel, f"<b>{sel}</b>", 1)
            back += f"<br><br><i>{example}</i>"
            if w.sentence_translation:
                back += f"<br>{html.escape(_flat(w.sentence_translation))}"
        out.append(f"{front}\t{back}\t{_tag(w.game or '')}")
    return out


def export_words(words: list[SavedWord], path: str | Path, fmt: str) -> int:
    """fmt: anki | csv. Возвращает число слов."""
    path = Path(path)
    if fmt == "anki":
        path.write_text("\n".join(anki_lines(words)) + "\n", encoding="utf-8")
        return len(words)
    with path.open("w", encoding="utf-8-sig", newline="") as f:  # BOM: Excel откроет кириллицу правильно
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["Слово", "Перевод", "Транскрипция", "Часть речи", "Пример", "Перевод примера", "Игра", "Дата"])
        import datetime as dt

        for w in reversed(words):
            writer.writerow(
                [
                    _flat(w.word),
                    _flat(w.translation),
                    _flat(w.ipa),
                    _flat(w.pos),
                    _flat(w.sentence),
                    _flat(w.sentence_translation),
                    w.game or "",
                    dt.datetime.fromtimestamp(w.created_at).strftime("%Y-%m-%d %H:%M") if w.created_at else "",
                ]
            )
    return len(words)
