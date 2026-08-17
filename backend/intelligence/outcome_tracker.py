"""
outcome_tracker.py — фиксация рекомендаций Argus и их результатов.

Жизненный цикл:
    record_recommendation() → record_action() → record_result()
    → (опционально) export_to_learning() → LearningBrain

НЕ выполняет действия на WB/Ozon.
НЕ считает результат сразу после рекомендации.
НЕ делает HTTP / LLM.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import replace

from backend.intelligence.interfaces import IIntelligenceStore
from backend.intelligence.learning import (
    ActionOutcome,
    OutcomeDirection as LearningOutcomeDirection,
)
from backend.intelligence.outcomes import (
    MetricAnalysis,
    OutcomeDirection,
    RecommendationOutcome,
)

log = logging.getLogger("selleros.intelligence.outcome_tracker")

# Метрики, где меньше = лучше
_LOWER_IS_BETTER = frozenset({
    "returns", "return_rate", "cancel_rate", "cancellations",
    "ad_cost", "cpc", "cpm", "bounce", "bounce_rate",
    "refunds", "complaints",
})

# Минимальный относительный сдвиг, чтобы считать изменение значимым
_EPS = 1e-9
_SIGNIFICANT_REL = 0.02  # 2%


class OutcomeTracker:
    """
    Трекер рекомендаций → действий → результатов.

    learning_brain — опционально; export_to_learning() пишет в Learning Loop.
    """

    def __init__(
        self,
        store: IIntelligenceStore,
        learning_brain=None,
    ) -> None:
        self._store = store
        self._learning = learning_brain

    # ──────────────────────────── lifecycle ─────────────────────────────── #

    async def record_recommendation(
        self,
        user_hash: str,
        category: str,
        article: str | None,
        recommendation_type: str,
        recommendation_action: str,
        recommendation_confidence: float,
        evidence_ids: list[str] | None = None,
    ) -> RecommendationOutcome:
        """Зафиксировать выданную рекомендацию. Результат = UNKNOWN."""
        if not user_hash:
            raise ValueError("user_hash обязателен (raw user_id запрещён)")

        outcome = RecommendationOutcome(
            id=str(uuid.uuid4()),
            user_hash=user_hash,
            category=category,
            article=article,
            recommendation_type=recommendation_type,
            recommendation_action=recommendation_action,
            recommendation_confidence=max(0.0, min(1.0, float(recommendation_confidence))),
            recommended_at=time.time(),
            evidence_ids=list(evidence_ids or []),
            outcome_direction=OutcomeDirection.UNKNOWN,
            outcome_score=None,
            confidence=0.0,
        )
        await self._store.save_recommendation_outcome(outcome)
        log.info(
            "OutcomeTracker.record_recommendation: %s/%s type=%s",
            category, article, recommendation_type,
        )
        return outcome

    async def record_action(
        self,
        outcome_id: str,
        action_taken: str,
        action_taken_at: float | None = None,
    ) -> RecommendationOutcome:
        """Зафиксировать, что продавец выполнил действие."""
        existing = await self._store.get_recommendation_outcome(outcome_id)
        if existing is None:
            raise KeyError(f"RecommendationOutcome {outcome_id!r} не найден")

        updated = replace(
            existing,
            action_taken=action_taken,
            action_taken_at=action_taken_at if action_taken_at is not None else time.time(),
        )
        await self._store.save_recommendation_outcome(updated)
        return updated

    async def record_result(
        self,
        outcome_id: str,
        metrics_before: dict,
        metrics_after: dict,
        period_start: float,
        period_end: float,
    ) -> RecommendationOutcome:
        """
        Зафиксировать метрики после периода наблюдения
        и рассчитать outcome_direction / score / confidence.
        """
        existing = await self._store.get_recommendation_outcome(outcome_id)
        if existing is None:
            raise KeyError(f"RecommendationOutcome {outcome_id!r} не найден")

        analysis = self.analyze_result(metrics_before or {}, metrics_after or {})

        updated = replace(
            existing,
            metrics_before=dict(metrics_before or {}),
            metrics_after=dict(metrics_after or {}),
            period_start=period_start,
            period_end=period_end,
            outcome_direction=analysis.outcome_direction,
            outcome_score=analysis.outcome_score,
            confidence=analysis.confidence,
        )
        await self._store.save_recommendation_outcome(updated)
        log.info(
            "OutcomeTracker.record_result: %s → %s score=%s conf=%.2f",
            outcome_id[:8], analysis.outcome_direction.value,
            analysis.outcome_score, analysis.confidence,
        )
        return updated

    async def get_outcome(self, outcome_id: str) -> RecommendationOutcome | None:
        return await self._store.get_recommendation_outcome(outcome_id)

    # ──────────────────────────── analysis ──────────────────────────────── #

    def analyze_result(
        self,
        metrics_before: dict,
        metrics_after: dict,
    ) -> MetricAnalysis:
        """
        Сравнить числовые before/after.

        Большинство улучшилось → POSITIVE
        Большинство ухудшилось → NEGATIVE
        Смешанно → MIXED
        Нет данных → UNKNOWN
        """
        if not metrics_after:
            return MetricAnalysis(
                outcome_direction=OutcomeDirection.UNKNOWN,
                outcome_score=None,
                confidence=0.0,
            )

        before = metrics_before or {}
        after = metrics_after or {}

        deltas: list[float] = []
        improved = 0
        worsened = 0

        for key, after_val in after.items():
            if key not in before:
                continue
            b = _as_number(before[key])
            a = _as_number(after_val)
            if b is None or a is None:
                continue

            lower_better = key.lower() in _LOWER_IS_BETTER
            rel = (a - b) / max(abs(b), _EPS)
            if lower_better:
                rel = -rel

            if abs(rel) < _SIGNIFICANT_REL:
                # незначимое изменение — не считаем
                deltas.append(0.0)
                continue

            deltas.append(max(-1.0, min(1.0, rel)))
            if rel > 0:
                improved += 1
            else:
                worsened += 1

        comparisons = improved + worsened
        if comparisons == 0:
            # есть after, но нет валидных числовых пар / все изменения незначимы
            has_any_numeric = any(
                _as_number(v) is not None for v in after.values()
            ) and any(_as_number(v) is not None for v in before.values())
            return MetricAnalysis(
                outcome_direction=OutcomeDirection.UNKNOWN,
                outcome_score=None,
                confidence=0.10 if has_any_numeric else 0.0,
                comparisons=0,
            )

        if improved > 0 and worsened > 0:
            direction = OutcomeDirection.MIXED
        elif improved > worsened:
            direction = OutcomeDirection.POSITIVE
        elif worsened > improved:
            direction = OutcomeDirection.NEGATIVE
        else:
            direction = OutcomeDirection.MIXED

        # score — среднее значимых относительных изменений, clamp [-1, 1]
        significant = [d for d in deltas if abs(d) >= _SIGNIFICANT_REL]
        if significant:
            score = max(-1.0, min(1.0, sum(significant) / len(significant)))
        else:
            score = None

        # confidence растёт с числом сравнений, потолок 0.85
        confidence = min(0.85, 0.20 + comparisons * 0.15)

        return MetricAnalysis(
            outcome_direction=direction,
            outcome_score=round(score, 4) if score is not None else None,
            confidence=round(confidence, 4),
            comparisons=comparisons,
            improved=improved,
            worsened=worsened,
        )

    # ──────────────────────────── LearningBrain bridge ──────────────────── #

    async def export_to_learning(self, outcome_id: str) -> ActionOutcome | None:
        """
        Конвертировать завершённый RecommendationOutcome → ActionOutcome
        и записать в LearningBrain (если подключён).

        Не вызывается автоматически.
        """
        if self._learning is None:
            return None

        rec = await self.get_outcome(outcome_id)
        if rec is None:
            return None
        if rec.outcome_direction == OutcomeDirection.UNKNOWN:
            return None
        if rec.period_start is None or rec.period_end is None:
            return None

        # MIXED → NEUTRAL для Learning Loop
        direction_map = {
            OutcomeDirection.POSITIVE: LearningOutcomeDirection.POSITIVE,
            OutcomeDirection.NEGATIVE: LearningOutcomeDirection.NEGATIVE,
            OutcomeDirection.MIXED:    LearningOutcomeDirection.NEUTRAL,
            OutcomeDirection.UNKNOWN:  LearningOutcomeDirection.UNKNOWN,
        }

        action_outcome = ActionOutcome(
            id=str(uuid.uuid4()),
            user_hash=rec.user_hash,
            category=rec.category,
            article=rec.article,
            recommendation_type=rec.recommendation_type,
            action=rec.action_taken or rec.recommendation_action,
            period_start=rec.period_start,
            period_end=rec.period_end,
            created_at=time.time(),
            metrics_before=dict(rec.metrics_before),
            metrics_after=dict(rec.metrics_after),
            outcome_direction=direction_map[rec.outcome_direction],
            outcome_score=float(rec.outcome_score or 0.0),
            confidence=rec.confidence,
            evidence_ids=list(rec.evidence_ids),
            metadata={
                "recommendation_outcome_id": rec.id,
                "recommendation_action": rec.recommendation_action,
            },
        )
        await self._learning.record_outcome(action_outcome)
        return action_outcome


def _as_number(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", ".").strip())
        except ValueError:
            return None
    return None
