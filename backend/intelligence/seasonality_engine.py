"""
seasonality_engine.py — Seasonality Engine для Intelligence Layer.

Строит SeasonalityRecord из реально накопленных данных:

Источник 1: KnowledgeItem yandex_wordstat (found_phrase)
    Для каждой категории и каждого накопленного периода (YYYY-MM)
    вычисляем demand_index относительно среднего по всем периодам
    внутри категории.

    При наличии только одного периода — demand_index=1.0 (baseline),
    confidence=0.35 (недостаточно точек для нормировки).

Источник 2: Evidence с signal_type='seasonality'
    SignalExtractor мог извлечь период-хинты из текста ("ноябрь-декабрь",
    "летние модели"). Маппим их на месяц и создаём SeasonalityRecord
    с низкой confidence (текстовый вывод, не измерение).

Источник 3: Межкатегорийное сравнение за один период
    Если у нас несколько категорий за один и тот же период —
    вычисляем demand_index по горизонтали: index_cat = fp_cat / mean(fp_all).

────────────────────────────────────────────────────────────────────────────
НЕ создаёт синтетические данные.
Если данных недостаточно → возвращает [] без создания пустых записей.
────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
import re
import statistics
import time
import uuid
from collections import defaultdict

from backend.intelligence.interfaces import IIntelligenceStore
from backend.intelligence.models import (
    Evidence,
    SeasonalityRecord,
)

log = logging.getLogger("selleros.intelligence.seasonality_engine")


# Маппинг слов → месяц (1-12)
_MONTH_MAP: dict[str, int] = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4,
    "май": 5, "мая": 5, "июн": 6, "июл": 7, "август": 8,
    "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
    # сезоны → диапазон, берём средний месяц
    "зим": 1, "весен": 4, "весн": 4, "лет": 7, "осен": 10,
    "новогодн": 12, "8 март": 3, "23 феврал": 2,
    "1 сентябр": 9, "день рожден": 0,  # день рождения → пропускаем (нет месяца)
    "черн пятниц": 11, "распродаж": 11,
}


def _period_to_month(period: str | None) -> int | None:
    """Преобразует строку 'YYYY-MM' в месяц 1-12. None если не парсится."""
    if not period:
        return None
    m = re.match(r"\d{4}-(\d{2})", period)
    if m:
        return int(m.group(1))
    return None


def _hint_to_month(hint: str) -> int | None:
    """Преобразует текстовый период-хинт в месяц 1-12."""
    text = hint.lower()
    for keyword, month in _MONTH_MAP.items():
        if keyword in text and month != 0:
            return month
    return None


class SeasonalityEngine:
    """
    Движок построения сезонности из реальных накопленных данных.

    build_from_demand_items(region, source_id)   → list[SeasonalityRecord]
    build_from_cross_category(region, source_id) → list[SeasonalityRecord]
    build_from_evidence(evidences)               → list[SeasonalityRecord]
    get_demand_profile(category, region)         → dict[int, float]
    """

    def __init__(self, store: IIntelligenceStore) -> None:
        self._store = store

    # ────────────────────────── из demand time-series ───────────────────── #

    async def build_from_demand_items(
        self,
        *,
        region: str = "RU",
        source_id: str = "yandex_wordstat",
        save: bool = True,
    ) -> list[SeasonalityRecord]:
        """
        Для каждой категории загружаем wordstat items с found_phrase.
        Вычисляем demand_index = fp_period / mean(fp_all_periods).

        Одна точка → demand_index=1.0 (baseline), confidence=0.35.
        N точек → настоящий индекс, confidence растёт с N.
        """
        # Загружаем все items из источника (все категории)
        all_items = await self._store.search_items(source_id=source_id, limit=500)

        # Группируем: category → {period: found_phrase}
        cat_periods: dict[str, dict[str, int]] = defaultdict(dict)
        for it in all_items:
            fp = (it.metadata or {}).get("found_phrase")
            if fp and fp > 0 and it.period and it.category:
                # Берём максимум, если несколько items за период
                existing = cat_periods[it.category].get(it.period, 0)
                cat_periods[it.category][it.period] = max(existing, fp)

        if not cat_periods:
            log.debug("SeasonalityEngine: нет demand data для построения seasonality")
            return []

        records: list[SeasonalityRecord] = []
        now = time.time()

        for category, period_data in cat_periods.items():
            periods = sorted(period_data.keys())
            found_values = [period_data[p] for p in periods]
            mean_fp = statistics.mean(found_values) if found_values else 0

            for period_str in periods:
                month = _period_to_month(period_str)
                if month is None:
                    continue

                fp = period_data[period_str]
                if mean_fp > 0:
                    demand_index = round(fp / mean_fp, 4)
                else:
                    demand_index = 1.0

                n = len(periods)
                if n == 1:
                    confidence = 0.35   # Только baseline
                    note = "single-period baseline"
                else:
                    confidence = round(min(0.80, 0.45 + 0.05 * n), 4)
                    note = f"{n}-period time series"

                year_str = period_str[:4] if len(period_str) >= 4 else "2026"
                try:
                    period_year = int(year_str)
                except ValueError:
                    period_year = 2026

                rec = SeasonalityRecord(
                    id=str(uuid.uuid4()),
                    category=category,
                    region=region,
                    month=month,
                    demand_index=demand_index,
                    source_id=source_id,
                    period_year=period_year,
                    created_at=now,
                    confidence=confidence,
                    week=None,
                )
                # Используем metadata через model_fields — SeasonalityRecord
                # не имеет metadata в схеме; храним extra в TrendRecord или
                # пишем в supporting_data через Evidence. Здесь только save.
                records.append(rec)

                if save:
                    await self._store.save_seasonality(rec)
                    log.info(
                        "SeasonalityEngine: saved cat=%r month=%d index=%.4f conf=%.2f (%s)",
                        category, month, demand_index, confidence, note,
                    )

        return records

    # ──────────────────────── межкатегорийное сравнение ─────────────────── #

    async def build_from_cross_category(
        self,
        *,
        region: str = "RU",
        source_id: str = "yandex_wordstat",
        save: bool = True,
    ) -> list[SeasonalityRecord]:
        """
        Для каждого периода сравниваем found_phrase разных категорий.

        demand_index_cat_period = fp_cat / mean(fp_all_cats_same_period).

        Это работает даже при единственном периоде, если есть ≥2 категории.
        """
        all_items = await self._store.search_items(source_id=source_id, limit=500)

        # Группируем: period → {category: found_phrase}
        period_cats: dict[str, dict[str, int]] = defaultdict(dict)
        for it in all_items:
            fp = (it.metadata or {}).get("found_phrase")
            if fp and fp > 0 and it.period and it.category:
                existing = period_cats[it.period].get(it.category, 0)
                period_cats[it.period][it.category] = max(existing, fp)

        if not period_cats:
            return []

        records: list[SeasonalityRecord] = []
        now = time.time()

        for period_str, cat_data in period_cats.items():
            if len(cat_data) < 2:
                log.debug(
                    "SeasonalityEngine: cross-cat пропускаем %r — только 1 категория",
                    period_str,
                )
                continue

            month = _period_to_month(period_str)
            if month is None:
                continue

            mean_fp = statistics.mean(cat_data.values())
            year_str = period_str[:4]
            try:
                period_year = int(year_str)
            except ValueError:
                period_year = 2026

            for category, fp in cat_data.items():
                demand_index = round(fp / mean_fp, 4) if mean_fp else 1.0
                confidence = round(min(0.75, 0.40 + 0.05 * len(cat_data)), 4)

                rec = SeasonalityRecord(
                    id=str(uuid.uuid4()),
                    category=category,
                    region=region,
                    month=month,
                    demand_index=demand_index,
                    source_id=source_id,
                    period_year=period_year,
                    created_at=now,
                    confidence=confidence,
                    week=None,
                )
                records.append(rec)

                if save:
                    await self._store.save_seasonality(rec)
                    log.info(
                        "SeasonalityEngine: cross-cat cat=%r month=%d index=%.4f conf=%.2f",
                        category, month, demand_index, confidence,
                    )

        return records

    # ────────────────────── из Evidence seasonality-сигналов ─────────────── #

    async def build_from_evidence(
        self,
        evidences: list[Evidence],
        *,
        region: str = "RU",
        source_id: str = "yandex_search",
        save: bool = True,
    ) -> list[SeasonalityRecord]:
        """
        Строит SeasonalityRecord из Evidence с signal_type='seasonality'.

        demand_index определить из текста невозможно точно → 1.2 (up) /
        0.8 (down) / 1.0 (neutral) в зависимости от direction.
        confidence ≤ 0.55 (это текстовый сигнал, не измерение).
        """
        seas_evs = [
            ev for ev in evidences
            if (ev.supporting_data or {}).get("signal_type") == "seasonality"
        ]
        if not seas_evs:
            return []

        records: list[SeasonalityRecord] = []
        now = time.time()

        for ev in seas_evs:
            data = ev.supporting_data or {}
            period_hint = data.get("period_hint", "")
            category    = data.get("category")
            direction   = data.get("direction", "up")

            month = _hint_to_month(period_hint) if period_hint else None
            if month is None:
                log.debug(
                    "SeasonalityEngine: не удалось определить месяц из hint=%r",
                    period_hint,
                )
                continue

            demand_index = 1.2 if direction == "up" else (
                           0.8  if direction == "down" else 1.0)
            confidence   = round(min(0.55, ev.confidence * 0.70), 4)

            rec = SeasonalityRecord(
                id=str(uuid.uuid4()),
                category=category or "unknown",
                region=region,
                month=month,
                demand_index=demand_index,
                source_id=source_id,
                period_year=2026,
                created_at=now,
                confidence=confidence,
                week=None,
            )
            records.append(rec)

            if save:
                await self._store.save_seasonality(rec)
                log.info(
                    "SeasonalityEngine: from evidence cat=%r month=%d idx=%.1f conf=%.2f",
                    rec.category, month, demand_index, confidence,
                )

        return records

    # ───────────────────────────── retrieval ──────────────────────────────── #

    async def get_demand_profile(
        self,
        category: str,
        region: str = "RU",
    ) -> dict[int, float]:
        """
        Возвращает профиль спроса по месяцам: {month: demand_index}.
        Если несколько записей за месяц — берём с максимальной confidence.
        Если данных нет — возвращает {}.
        """
        profile: dict[int, tuple[float, float]] = {}  # month → (index, confidence)

        for month in range(1, 13):
            recs = await self._store.get_seasonality(category, region, month)
            for rec in recs:
                existing = profile.get(rec.month)
                if existing is None or rec.confidence > existing[1]:
                    profile[rec.month] = (rec.demand_index, rec.confidence)

        return {month: idx for month, (idx, _) in sorted(profile.items())}
