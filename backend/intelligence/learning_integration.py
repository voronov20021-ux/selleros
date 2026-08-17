"""
learning_integration.py — Outcome → Learning Loop bridge v1.

RecommendationOutcome → LearningSignal (+ ActionOutcome) → LearningBrain

UNKNOWN не создаёт обучающий сигнал.
Дедупликация по source_outcome_id.
Без HTTP / ML / автозапуска.
"""

from __future__ import annotations

import logging
import time
import uuid

from backend.intelligence.learning import (
    ActionOutcome,
    LearningSignal,
    LearningSignalType,
    OutcomeDirection as LearningOutcomeDirection,
)
from backend.intelligence.outcomes import OutcomeDirection, RecommendationOutcome

log = logging.getLogger("selleros.intelligence.learning_integration")

_DIR_TO_SIGNAL: dict[OutcomeDirection, LearningSignalType] = {
    OutcomeDirection.POSITIVE: LearningSignalType.SUCCESS,
    OutcomeDirection.NEGATIVE: LearningSignalType.FAILURE,
    OutcomeDirection.MIXED:    LearningSignalType.MIXED_PATTERN,
}

_DIR_TO_LEARNING: dict[OutcomeDirection, LearningOutcomeDirection] = {
    OutcomeDirection.POSITIVE: LearningOutcomeDirection.POSITIVE,
    OutcomeDirection.NEGATIVE: LearningOutcomeDirection.NEGATIVE,
    OutcomeDirection.MIXED:    LearningOutcomeDirection.NEUTRAL,
}


class OutcomeLearningIntegrator:
    """
    Замыкает цепочку Outcome → LearningBrain.

    learning_brain — LearningBrain | None
    При None / ошибках → graceful None.
    """

    def __init__(self, learning_brain=None) -> None:
        self._brain = learning_brain

    async def integrate(
        self,
        outcome: RecommendationOutcome,
    ) -> LearningSignal | None:
        """
        POSITIVE / NEGATIVE / MIXED → LearningSignal
        UNKNOWN → None
        Повторный вызов с тем же outcome.id → существующий сигнал (без дубля).
        """
        if self._brain is None:
            return None

        if outcome is None:
            return None

        try:
            if outcome.outcome_direction == OutcomeDirection.UNKNOWN:
                return None

            signal_type = _DIR_TO_SIGNAL.get(outcome.outcome_direction)
            if signal_type is None:
                return None

            # Idempotency: уже интегрировали этот outcome?
            existing = await self._brain.find_signal_by_source_outcome(outcome.id)
            if existing is not None:
                return existing

            metrics_summary = _metrics_summary(
                outcome.metrics_before or {},
                outcome.metrics_after or {},
            )

            action_taken = outcome.action_taken or outcome.recommendation_action
            claim = _build_claim(outcome, action_taken)

            meta = {
                "category": outcome.category,
                "recommendation_type": outcome.recommendation_type,
                "action_taken": action_taken,
                "outcome_direction": outcome.outcome_direction.value,
                "outcome_score": outcome.outcome_score,
                "confidence": outcome.confidence,
                "metrics_summary": metrics_summary,
                "source_outcome_id": outcome.id,
            }

            # ActionOutcome нужен LearningBrain.analyze → LearningContextSource
            ao_id = f"ao_{outcome.id}"
            action_outcome = ActionOutcome(
                id=ao_id,
                user_hash=outcome.user_hash,  # уже hash, не raw user_id
                category=outcome.category,
                article=outcome.article,
                recommendation_type=outcome.recommendation_type,
                action=action_taken,
                period_start=outcome.period_start or outcome.recommended_at,
                period_end=outcome.period_end or time.time(),
                created_at=time.time(),
                metrics_before=dict(outcome.metrics_before or {}),
                metrics_after=dict(outcome.metrics_after or {}),
                outcome_direction=_DIR_TO_LEARNING[outcome.outcome_direction],
                outcome_score=float(outcome.outcome_score or 0.0),
                confidence=float(outcome.confidence or 0.0),
                evidence_ids=[],  # не тащим UUID в learning context path
                metadata={
                    "source_outcome_id": outcome.id,
                    "recommendation_type": outcome.recommendation_type,
                },
            )
            await self._brain.record_outcome(action_outcome)

            signal = LearningSignal(
                id=str(uuid.uuid4()),
                outcome_id=ao_id,
                signal_type=signal_type,
                claim=claim,
                confidence=float(outcome.confidence or 0.0),
                evidence_ids=[],
                metadata=meta,
                created_at=time.time(),
            )
            saved = await self._brain.record_signal(signal)
            log.info(
                "OutcomeLearningIntegrator: %s → %s (source=%s)",
                outcome.outcome_direction.value,
                signal_type.value,
                outcome.id[:8],
            )
            return saved

        except Exception as exc:
            log.warning(
                "OutcomeLearningIntegrator.integrate failed: %s", exc,
            )
            return None


def _metrics_summary(before: dict, after: dict) -> dict:
    """Компактный summary только по общим числовым ключам."""
    summary: dict = {}
    for key, after_val in (after or {}).items():
        if key not in (before or {}):
            continue
        b = _num(before[key])
        a = _num(after_val)
        if b is None or a is None:
            continue
        summary[str(key)] = {"before": b, "after": a}
    return summary


def _num(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _build_claim(outcome: RecommendationOutcome, action_taken: str) -> str:
    direction = outcome.outcome_direction.value
    cat = outcome.category or "?"
    rtype = outcome.recommendation_type or "?"
    score = outcome.outcome_score
    score_part = f", score={score:+.2f}" if score is not None else ""
    return (
        f"[OBSERVATION] {direction.upper()}: действие «{action_taken}» "
        f"({rtype}) в «{cat}»{score_part}."
    )
