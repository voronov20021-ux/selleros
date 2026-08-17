"""
learning_brain_interface.py — контракт провайдера Learning Brain.

LearningBrainProvider — точка расширения для будущего ML/LLM мозга.
Текущая реализация — RuleBasedLearningBrainProvider (без LLM, без HTTP).

Подключение новой модели = реализовать LearningBrainProvider
и передать в LearningBrain(provider=...). Argus не меняется.
"""

from __future__ import annotations

import logging
import time
import uuid
from abc import ABC, abstractmethod

from backend.intelligence.learning import (
    ActionOutcome,
    LearningAssessment,
    LearningSignal,
    LearningSignalType,
    OutcomeDirection,
)

log = logging.getLogger("selleros.intelligence.learning_brain_interface")

_MIN_SAMPLE: int = 5
_POSITIVE_RATE: float = 0.70
_NEGATIVE_RATE: float = 0.30


class LearningBrainProvider(ABC):
    """Абстрактный провайдер анализа ActionOutcome → LearningAssessment."""

    @abstractmethod
    async def analyze(self, outcomes: list[ActionOutcome]) -> LearningAssessment:
        """Построить LearningAssessment из списка outcomes."""


class RuleBasedLearningBrainProvider(LearningBrainProvider):
    """
    Rule-based анализ без LLM/ML.

    Правила:
        sample_size < 5          → LOW_CONFIDENCE
        success_rate >= 0.70     → POSITIVE_PATTERN
        success_rate <= 0.30     → NEGATIVE_PATTERN
        иначе                    → MIXED_PATTERN
    """

    async def analyze(self, outcomes: list[ActionOutcome]) -> LearningAssessment:
        if not outcomes:
            return LearningAssessment(
                category="",
                action=None,
                sample_size=0,
                success_count=0,
                failure_count=0,
                success_rate=0.0,
                confidence=0.0,
                signals=[],
                warnings=["Недостаточно данных: outcomes пуст."],
                generated_at=time.time(),
            )

        category = outcomes[0].category
        actions = {o.action for o in outcomes}
        action = next(iter(actions)) if len(actions) == 1 else None

        success = [o for o in outcomes if o.outcome_direction == OutcomeDirection.POSITIVE]
        failure = [o for o in outcomes if o.outcome_direction == OutcomeDirection.NEGATIVE]
        ambiguous = [
            o for o in outcomes
            if o.outcome_direction in (OutcomeDirection.NEUTRAL, OutcomeDirection.UNKNOWN)
        ]

        sample_size = len(outcomes)
        success_count = len(success)
        failure_count = len(failure)
        # success_rate считаем только по определённым результатам
        decided = success_count + failure_count
        success_rate = (success_count / decided) if decided > 0 else 0.0

        signals: list[LearningSignal] = []
        warnings: list[str] = []

        # Per-outcome signals
        for o in success:
            signals.append(_signal(
                LearningSignalType.SUCCESS,
                f"[OBSERVATION] Успешное действие «{o.action}» в «{o.category}».",
                o.confidence,
                o.id,
                o.evidence_ids,
            ))
        for o in failure:
            signals.append(_signal(
                LearningSignalType.FAILURE,
                f"[OBSERVATION] Неуспешное действие «{o.action}» в «{o.category}».",
                o.confidence,
                o.id,
                o.evidence_ids,
            ))
        for o in ambiguous:
            signals.append(_signal(
                LearningSignalType.AMBIGUOUS,
                f"[OBSERVATION] Неоднозначный результат действия «{o.action}».",
                max(0.20, o.confidence * 0.7),
                o.id,
                o.evidence_ids,
            ))

        all_ev = _collect_evidence_ids(outcomes)

        # Pattern / confidence rules
        if sample_size < _MIN_SAMPLE:
            warnings.append(
                f"Недостаточный sample size ({sample_size} < {_MIN_SAMPLE})."
            )
            signals.append(_signal(
                LearningSignalType.LOW_CONFIDENCE,
                f"[INFERENCE] Недостаточно наблюдений ({sample_size}) для устойчивого вывода.",
                0.20,
                None,
                all_ev,
            ))
            pattern_conf = min(0.35, 0.10 + sample_size * 0.04)
        elif success_rate >= _POSITIVE_RATE:
            signals.append(_signal(
                LearningSignalType.POSITIVE_PATTERN,
                (
                    f"[INFERENCE] Повторяющийся позитивный паттерн: "
                    f"success_rate={success_rate:.0%} (n={sample_size})."
                ),
                min(0.85, 0.50 + success_rate * 0.30),
                None,
                all_ev,
                {"success_rate": round(success_rate, 3), "sample_size": sample_size},
            ))
            pattern_conf = min(0.85, 0.50 + success_rate * 0.30)
        elif success_rate <= _NEGATIVE_RATE:
            signals.append(_signal(
                LearningSignalType.NEGATIVE_PATTERN,
                (
                    f"[INFERENCE] Повторяющийся негативный паттерн: "
                    f"success_rate={success_rate:.0%} (n={sample_size})."
                ),
                min(0.85, 0.50 + (1.0 - success_rate) * 0.30),
                None,
                all_ev,
                {"success_rate": round(success_rate, 3), "sample_size": sample_size},
            ))
            pattern_conf = min(0.85, 0.50 + (1.0 - success_rate) * 0.30)
        else:
            signals.append(_signal(
                LearningSignalType.MIXED_PATTERN,
                (
                    f"[INFERENCE] Смешанные результаты: "
                    f"success_rate={success_rate:.0%} (n={sample_size})."
                ),
                0.45,
                None,
                all_ev,
                {"success_rate": round(success_rate, 3), "sample_size": sample_size},
            ))
            pattern_conf = 0.45

        # Conflict: одновременно много success и failure
        if success_count >= 2 and failure_count >= 2 and sample_size >= _MIN_SAMPLE:
            signals.append(_signal(
                LearningSignalType.CONFLICT,
                (
                    f"[OBSERVATION] Конфликтующие результаты: "
                    f"{success_count} успехов / {failure_count} неудач."
                ),
                0.50,
                None,
                all_ev,
            ))
            warnings.append("Конфликтующие результаты в выборке.")
            pattern_conf = min(pattern_conf, 0.50)

        return LearningAssessment(
            category=category,
            action=action,
            sample_size=sample_size,
            success_count=success_count,
            failure_count=failure_count,
            success_rate=round(success_rate, 4),
            confidence=round(pattern_conf, 4),
            signals=signals,
            warnings=warnings,
            generated_at=time.time(),
        )


def _signal(
    signal_type: LearningSignalType,
    claim: str,
    confidence: float,
    outcome_id: str | None,
    evidence_ids: list[str],
    metadata: dict | None = None,
) -> LearningSignal:
    return LearningSignal(
        id=str(uuid.uuid4()),
        outcome_id=outcome_id,
        signal_type=signal_type,
        claim=claim,
        confidence=round(max(0.0, min(1.0, confidence)), 4),
        evidence_ids=list(evidence_ids),
        metadata=metadata or {},
        created_at=time.time(),
    )


def _collect_evidence_ids(outcomes: list[ActionOutcome]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for o in outcomes:
        for eid in o.evidence_ids:
            if eid and eid not in seen:
                seen.add(eid)
                result.append(eid)
    return result
