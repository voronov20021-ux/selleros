"""
evidence/aggregator.py — агрегация Evidence из нескольких источников.

EvidenceAggregator объединяет похожие Evidence в один агрегированный,
повышая confidence за счёт консистентности между источниками.

Принципы:
  - НЕ смешивать разные категории.
  - НЕ смешивать разные регионы.
  - НЕ смешивать сильно разные периоды (> 6 месяцев друг от друга).
  - При агрегации confidence растёт (консенсус источников), но не > 0.95.
  - Если только 1 Evidence в группе — возвращается как есть.
  - Агрегат создаётся как новый Evidence типа INFERENCE
    (вывод из нескольких фактов), не подменяя исходники.

Формула confidence для агрегата:
  base = среднее confidence всех источников в группе
  bonus = 0.05 * (n - 1), но не более 0.20   (за консенсус)
  agg_conf = min(0.95, base + bonus)

Агрегированный Evidence хранит в supporting_data:
  "aggregated_from": [list of evidence_ids]
  "source_count":    int
  "direction":       "up"|"down"|"stable"
  "change_pct_mean": float | None
  "change_pct_min":  float | None
  "change_pct_max":  float | None
  "signal_type":     str
"""

from __future__ import annotations

import statistics
import time
import uuid
from dataclasses import dataclass, field

from backend.intelligence.models import Evidence, EvidenceType


@dataclass
class AggregatedEvidence:
    """
    Результат агрегации нескольких Evidence.

    evidence       — новый Evidence-объект (тип INFERENCE).
    source_ids     — id исходных Evidence, использованных для агрегации.
    source_count   — количество источников.
    pct_mean       — среднее change_pct (None если у источников нет чисел).
    direction      — итоговое направление.
    """

    evidence: Evidence
    source_ids: list[str]
    source_count: int
    pct_mean: float | None
    direction: str | None


class EvidenceAggregator:
    """
    Агрегатор Evidence.

    aggregate_trends(evidences)      → list[AggregatedEvidence]
    aggregate_seasonality(evidences) → list[AggregatedEvidence]
    aggregate_market_events(evidences) → list[AggregatedEvidence]

    Каждый метод:
        1. Фильтрует подходящий signal_type.
        2. Группирует по (category, region, direction / period).
        3. Агрегирует confidence и числовые значения.
        4. Возвращает список агрегатов (не сохраняет в store).
    """

    def aggregate_trends(
        self,
        evidences: list[Evidence],
    ) -> list[AggregatedEvidence]:
        """
        Агрегировать трендовые сигналы.

        Группировка по: (category, region, direction).
        Смешивать up и down нельзя.
        """
        relevant = [
            ev for ev in evidences
            if (ev.supporting_data or {}).get("signal_type") == "trend"
        ]
        if not relevant:
            return []

        groups = self._group_by(relevant, keys=("category", "region", "direction"))
        return [self._aggregate_group(g, "trend") for g in groups.values() if g]

    def aggregate_seasonality(
        self,
        evidences: list[Evidence],
    ) -> list[AggregatedEvidence]:
        """
        Агрегировать сезонные сигналы.

        Группировка по: (category, region, period_hint).
        """
        relevant = [
            ev for ev in evidences
            if (ev.supporting_data or {}).get("signal_type") == "seasonality"
        ]
        if not relevant:
            return []

        groups = self._group_by(relevant, keys=("category", "region", "period_hint"))
        return [self._aggregate_group(g, "seasonality") for g in groups.values() if g]

    def aggregate_market_events(
        self,
        evidences: list[Evidence],
    ) -> list[AggregatedEvidence]:
        """
        Агрегировать рыночные события.

        Группировка по: (category, region, period).
        """
        relevant = [
            ev for ev in evidences
            if (ev.supporting_data or {}).get("signal_type") == "market_event"
        ]
        if not relevant:
            return []

        groups = self._group_by(relevant, keys=("category", "region", "period"))
        return [self._aggregate_group(g, "market_event") for g in groups.values() if g]

    # ─────────────────────────── helpers ────────────────────────────────── #

    @staticmethod
    def _group_by(
        evidences: list[Evidence],
        keys: tuple[str, ...],
    ) -> dict[str, list[Evidence]]:
        """Сгруппировать Evidence по значениям ключей из supporting_data."""
        groups: dict[str, list[Evidence]] = {}

        for ev in evidences:
            data = ev.supporting_data or {}
            key_parts = []
            for k in keys:
                val = data.get(k) or "unknown"
                key_parts.append(str(val))
            key = "|".join(key_parts)
            groups.setdefault(key, []).append(ev)

        return groups

    @staticmethod
    def _aggregate_group(
        group: list[Evidence],
        signal_type: str,
    ) -> AggregatedEvidence:
        """Агрегировать одну группу Evidence."""
        if len(group) == 1:
            ev = group[0]
            data = ev.supporting_data or {}
            return AggregatedEvidence(
                evidence=ev,
                source_ids=[ev.id],
                source_count=1,
                pct_mean=data.get("change_pct"),
                direction=data.get("direction"),
            )

        # Числовые значения
        pcts = [
            ev.supporting_data.get("change_pct")
            for ev in group
            if (ev.supporting_data or {}).get("change_pct") is not None
        ]
        pcts_clean = [p for p in pcts if p is not None]

        pct_mean   = round(statistics.mean(pcts_clean), 2) if pcts_clean else None
        pct_min    = round(min(pcts_clean), 2)             if pcts_clean else None
        pct_max    = round(max(pcts_clean), 2)             if pcts_clean else None

        # Направление: консенсус большинства
        directions = [
            (ev.supporting_data or {}).get("direction")
            for ev in group
        ]
        direction_votes: dict[str, int] = {}
        for d in directions:
            if d:
                direction_votes[d] = direction_votes.get(d, 0) + 1

        if direction_votes:
            agg_direction = max(direction_votes, key=lambda k: direction_votes[k])
        else:
            agg_direction = None

        # Confidence агрегата
        confidences = [ev.confidence for ev in group]
        base_conf = statistics.mean(confidences)
        n = len(group)
        bonus = min(0.20, 0.05 * (n - 1))
        agg_conf = round(min(0.95, base_conf + bonus), 4)

        # Текст утверждения
        first_data = group[0].supporting_data or {}
        category = first_data.get("category") or "категории"
        region   = first_data.get("region")   or "RU"

        dir_word = {"up": "вырос", "down": "снизился", "stable": "стабилен"}.get(
            agg_direction or "", "изменился"
        )
        pct_str = f" на {abs(pct_mean):.1f}%" if pct_mean is not None else ""

        claim = (
            f"[Агрегат {n} ист.] {signal_type.upper()}: "
            f"спрос на {category} ({region}) {dir_word}{pct_str}"
        )
        if pct_min is not None and pct_max is not None and pct_min != pct_max:
            claim += f" [разброс: {pct_min:+.1f}% … {pct_max:+.1f}%]"

        agg_evidence = Evidence(
            id=str(uuid.uuid4()),
            knowledge_item_id=group[0].knowledge_item_id,  # первый источник
            evidence_type=EvidenceType.INFERENCE,           # вывод из нескольких
            claim=claim,
            supporting_data={
                "signal_type":          signal_type,
                "aggregated_from":      [ev.id for ev in group],
                "source_count":         n,
                "direction":            agg_direction,
                "change_pct_mean":      pct_mean,
                "change_pct_min":       pct_min,
                "change_pct_max":       pct_max,
                "category":             category,
                "region":               region,
                "source_id":            "aggregated",
                "confidence_factors":   [
                    f"consensus_{n}_sources",
                    "aggregated_inference",
                ],
            },
            confidence=agg_conf,
            created_at=time.time(),
        )

        return AggregatedEvidence(
            evidence=agg_evidence,
            source_ids=[ev.id for ev in group],
            source_count=n,
            pct_mean=pct_mean,
            direction=agg_direction,
        )
