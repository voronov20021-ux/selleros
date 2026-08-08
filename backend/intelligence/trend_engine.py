"""
trend_engine.py — Trend Engine для Intelligence Layer.

Строит TrendRecord из реально накопленных данных:

Источник 1: Evidence с signal_type='trend'
    SignalExtractor извлёк явные тренд-утверждения из текста поисковых
    результатов. TrendEngine группирует их по (category, region) и
    вычисляет консенсус направления → TrendRecord.

Источник 2: KnowledgeItem yandex_wordstat (found_phrase)
    Если для одной категории накоплено ≥2 items из разных периодов —
    вычисляем направление по динамике found_phrase.
    Если только 1 период — сохраняем TrendRecord(direction=STABLE,
    confidence=0.40) как базовую точку для будущего сравнения.

Источник 3: Ранжирование категорий по спросу
    Сравниваем found_phrase нескольких категорий за один период.
    Категория с самым высоким found_phrase → лидер спроса (relative trend).
    Это реальный сигнал при наличии ≥2 категорий.

────────────────────────────────────────────────────────────────────────────
НЕ создаёт синтетические данные.
Если данных недостаточно → возвращает [] без создания пустых записей.
────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
import statistics
import time
import uuid
from collections import defaultdict

from backend.intelligence.interfaces import IIntelligenceStore
from backend.intelligence.models import (
    Evidence,
    TrendDirection,
    TrendRecord,
)

log = logging.getLogger("selleros.intelligence.trend_engine")


class TrendEngine:
    """
    Движок построения трендов из реальных накопленных данных.

    build_from_evidence(evidences)      → list[TrendRecord]
    build_from_demand_items(category)   → list[TrendRecord]
    rank_categories(categories)         → list[dict]  (относительный спрос)
    """

    def __init__(self, store: IIntelligenceStore) -> None:
        self._store = store

    # ────────────────────────────────── из Evidence ──────────────────────── #

    async def build_from_evidence(
        self,
        evidences: list[Evidence],
        *,
        source_id: str = "yandex_wordstat",
        region: str = "RU",
        save: bool = True,
    ) -> list[TrendRecord]:
        """
        Построить TrendRecord из Evidence с signal_type='trend'.

        Группирует по (category, region, direction).
        Если в группе ≥1 evidence с явным direction → создаёт TrendRecord.
        Confidence = среднее по группе + бонус за консенсус.
        """
        trend_evs = [
            ev for ev in evidences
            if (ev.supporting_data or {}).get("signal_type") == "trend"
        ]
        if not trend_evs:
            return []

        # Группировка по (category, region, direction)
        groups: dict[tuple, list[Evidence]] = defaultdict(list)
        for ev in trend_evs:
            data = ev.supporting_data or {}
            key = (
                data.get("category") or "unknown",
                data.get("region")   or region,
                data.get("direction") or "stable",
            )
            groups[key].append(ev)

        records: list[TrendRecord] = []
        now = time.time()

        for (category, reg, direction_str), group in groups.items():
            direction = self._parse_direction(direction_str)

            # Средний change_pct по группе (только если есть числа)
            pcts = [
                (ev.supporting_data or {}).get("change_pct")
                for ev in group
                if (ev.supporting_data or {}).get("change_pct") is not None
            ]
            change_pct = round(statistics.mean(pcts), 2) if pcts else None

            # Confidence: среднее + небольшой бонус за кол-во источников
            base_conf = statistics.mean(ev.confidence for ev in group)
            bonus = min(0.10, 0.05 * (len(group) - 1))
            confidence = round(min(0.90, base_conf + bonus), 4)

            tr = TrendRecord(
                id=str(uuid.uuid4()),
                source_id=source_id,
                period_start=min(ev.created_at for ev in group),
                period_end=now,
                direction=direction,
                created_at=now,
                category=category,
                region=reg,
                change_pct=change_pct,
                confidence=confidence,
                metadata={
                    "built_from": "evidence_signals",
                    "evidence_count": len(group),
                    "evidence_ids": [ev.id for ev in group[:5]],
                },
            )
            records.append(tr)

            if save:
                await self._store.save_trend(tr)
                log.info(
                    "TrendEngine: saved TrendRecord category=%r dir=%s pct=%s conf=%.2f",
                    category, direction.value, change_pct, confidence,
                )

        return records

    # ──────────────────────────── из demand items ──────────────────────────── #

    async def build_from_demand_items(
        self,
        category: str,
        *,
        region: str = "RU",
        source_id: str = "yandex_wordstat",
        save: bool = True,
    ) -> list[TrendRecord]:
        """
        Построить TrendRecord из KnowledgeItem yandex_wordstat для категории.

        Если ≥2 items из разных периодов → direction из динамики found_phrase.
        Если 1 период → TrendRecord(direction=STABLE, confidence=0.40).
        Если нет items с found_phrase → возвращает [].
        """
        items = await self._store.search_items(
            source_id=source_id,
            category=category,
            limit=100,
        )

        # Фильтруем: только главные items с found_phrase
        demand_points: list[tuple[str, int]] = []
        for it in items:
            fp = (it.metadata or {}).get("found_phrase")
            if fp and fp > 0 and it.period:
                demand_points.append((it.period, fp))

        if not demand_points:
            log.debug("TrendEngine: нет demand points для %r", category)
            return []

        # Сортируем по периоду
        demand_points.sort(key=lambda x: x[0])
        now = time.time()

        if len(demand_points) == 1:
            # Единственная точка — базовая линия, direction=STABLE
            period_str, found = demand_points[0]
            tr = TrendRecord(
                id=str(uuid.uuid4()),
                source_id=source_id,
                period_start=now - 86400 * 30,
                period_end=now,
                direction=TrendDirection.STABLE,
                created_at=now,
                category=category,
                region=region,
                change_pct=None,
                confidence=0.40,
                metadata={
                    "built_from": "single_demand_point",
                    "period": period_str,
                    "found_phrase": found,
                    "note": "Single data point; direction=STABLE is baseline, not measured trend",
                },
            )
            if save:
                await self._store.save_trend(tr)
                log.info(
                    "TrendEngine: baseline TrendRecord cat=%r period=%s found=%d",
                    category, period_str, found,
                )
            return [tr]

        # ≥2 точки → измеряем направление
        first_period, first_found = demand_points[0]
        last_period,  last_found  = demand_points[-1]

        if first_found == 0:
            return []

        change_pct = round((last_found - first_found) / first_found * 100, 2)
        direction  = TrendDirection.UP if change_pct > 2 else (
                     TrendDirection.DOWN if change_pct < -2 else TrendDirection.STABLE)
        # Confidence растёт с количеством точек
        confidence = round(min(0.85, 0.50 + 0.05 * len(demand_points)), 4)

        tr = TrendRecord(
            id=str(uuid.uuid4()),
            source_id=source_id,
            period_start=now - 86400 * 30 * len(demand_points),
            period_end=now,
            direction=direction,
            created_at=now,
            category=category,
            region=region,
            change_pct=change_pct,
            confidence=confidence,
            metadata={
                "built_from":    "demand_time_series",
                "data_points":   len(demand_points),
                "first_period":  first_period,
                "last_period":   last_period,
                "first_found":   first_found,
                "last_found":    last_found,
            },
        )
        if save:
            await self._store.save_trend(tr)
            log.info(
                "TrendEngine: TrendRecord cat=%r dir=%s pct=%.1f%% conf=%.2f",
                category, direction.value, change_pct, confidence,
            )
        return [tr]

    # ─────────────────────────── ранжирование категорий ──────────────────── #

    async def rank_categories(
        self,
        categories: list[str],
        *,
        region: str = "RU",
        source_id: str = "yandex_wordstat",
        save: bool = True,
    ) -> list[dict]:
        """
        Сравнить категории по найденному found_phrase за последний период.

        Возвращает список dict, отсортированный по убыванию found_phrase:
            [{"category", "found_phrase", "demand_index", "period"}, ...]

        demand_index = found_phrase_i / mean(all found_phrase).
        Дополнительно сохраняет TrendRecord для каждой категории
        с direction из position относительно среднего.

        Если данных нет для категории — пропускаем.
        """
        rows: list[dict] = []

        for cat in categories:
            items = await self._store.search_items(
                source_id=source_id,
                category=cat,
                limit=10,
            )
            best: tuple[str, int] | None = None
            for it in items:
                fp = (it.metadata or {}).get("found_phrase")
                if fp and fp > 0:
                    if best is None or fp > best[1]:
                        best = (it.period or "", fp)

            if best:
                rows.append({
                    "category":    cat,
                    "found_phrase": best[1],
                    "period":      best[0],
                    "region":      region,
                })

        if not rows:
            return []

        mean_fp = statistics.mean(r["found_phrase"] for r in rows)

        now = time.time()
        result: list[dict] = []
        for row in sorted(rows, key=lambda r: r["found_phrase"], reverse=True):
            idx = round(row["found_phrase"] / mean_fp, 4) if mean_fp else 1.0
            row["demand_index"] = idx
            result.append(row)

            if save:
                direction = (TrendDirection.UP   if idx > 1.05 else
                             TrendDirection.DOWN  if idx < 0.95 else
                             TrendDirection.STABLE)
                tr = TrendRecord(
                    id=str(uuid.uuid4()),
                    source_id=source_id,
                    period_start=now - 86400 * 30,
                    period_end=now,
                    direction=direction,
                    created_at=now,
                    category=row["category"],
                    region=region,
                    change_pct=None,
                    confidence=0.60,
                    metadata={
                        "built_from":    "category_ranking",
                        "found_phrase":  row["found_phrase"],
                        "demand_index":  idx,
                        "period":        row["period"],
                        "mean_found":    round(mean_fp),
                        "categories_compared": len(rows),
                    },
                )
                await self._store.save_trend(tr)

        return result

    # ─────────────────────────── helpers ──────────────────────────────────── #

    @staticmethod
    def _parse_direction(raw: str) -> TrendDirection:
        mapping = {"up": TrendDirection.UP, "down": TrendDirection.DOWN}
        return mapping.get(raw, TrendDirection.STABLE)
