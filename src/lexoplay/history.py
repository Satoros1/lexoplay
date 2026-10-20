"""Кеш переводов на диске: та же реплика второй раз переводится мгновенно и бесплатно.

Реплики в новеллах повторяются: перезапуск главы, возврат к сохранению, одинаковые
ответы персонажей. Ищем сначала точное совпадение нормализованного текста, потом
похожую реплику среди последних в этой игре (OCR между кадрами чуть плавает).
Эти же строки показывает вкладка «История».
"""

from __future__ import annotations

import logging

from lexoplay.dedupe import LineRecord, normalize_text, similarity
from lexoplay.store import Store
from lexoplay.translate import TRANSLATION_VERSION, fix_punctuation

log = logging.getLogger(__name__)


class LineCache:
    """ver: переводы старых версий промпта не берём ни в кеш, ни в контекст: они переводились хуже."""

    def __init__(self, store: Store, *, threshold: float = 0.92, recent: int = 300, ver: int = TRANSLATION_VERSION) -> None:
        self._store = store
        self._threshold = threshold
        self._recent = recent
        self.ver = ver

    def find(self, exe: str, lang: str, text: str) -> LineRecord | None:
        norm = normalize_text(text)
        if not exe or len(norm) < 2:
            return None
        hit = self._store.line_find(exe, lang, norm, self.ver)
        if hit is None:
            best, best_score = None, 0.0
            for row in self._store.line_recent(exe, lang, self._recent, self.ver):
                # заведомо разные по длине не сравниваем: SequenceMatcher не бесплатный
                if abs(len(row.norm) - len(norm)) > max(len(norm), len(row.norm)) * 0.2:
                    continue
                score = similarity(norm, row.norm)
                if score > best_score:
                    best, best_score = row, score
            if best is not None and best_score >= self._threshold:
                hit = best
        if hit is None:
            return None
        self._store.line_seen(hit.id)
        return LineRecord(
            line_id=f"local-{hit.id}",
            speaker=hit.speaker,
            source=hit.source,
            translation=fix_punctuation(hit.source, hit.translation),
            norm=hit.norm,
        )

    def recent(self, exe: str, lang: str, limit: int) -> list[LineRecord]:
        """Последние реплики игры, от старых к новым: контекст для перевода в начале новой сессии."""
        if not exe or limit <= 0:
            return []
        rows = self._store.line_recent(exe, lang, limit, self.ver)
        return [
            LineRecord(f"local-{r.id}", r.speaker, r.source, r.translation, r.norm) for r in reversed(rows)
        ]

    def add(self, exe: str, lang: str, rec: LineRecord) -> None:
        if not exe or not rec.norm or not rec.translation:
            return
        try:
            self._store.line_add(exe, lang, rec.norm, rec.speaker, rec.source, rec.translation, self.ver)
        except Exception:
            log.exception("реплика не записалась в кеш")

    def retranslated(self, exe: str, lang: str, rec: LineRecord) -> None:
        """Перевели заново: прошлые переводы этой реплики заменить новым (или записать, если их не было)."""
        if not exe or not rec.norm:
            return
        try:
            if self._store.line_find(exe, lang, rec.norm) is None:
                self.add(exe, lang, rec)
            else:
                self._store.line_retranslated(exe, lang, rec.norm, rec.translation, self.ver)
        except Exception:
            log.exception("новый перевод не записался в кеш")
