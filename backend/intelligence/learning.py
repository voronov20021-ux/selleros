"""
learning.py — модели Learning Loop v1.

Память результатов действий Argus и продавцов.
Чистые dataclass-ы без зависимостей от хранилища/транспорта.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class OutcomeDirection(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL  = "neutral"
    UNKNOWN  = "unknown"


class LearningSignalType(str, Enum):
    POSITIVE_PATTERN = "POSITIVE_PATTERN"
    NEGATIVE_PATTERN = "NEGATIVE_PATTERN"
    MIXED_PATTERN    = "MIXED_PATTERN"
    LOW_CONFIDENCE   = "LOW_CONFIDENCE"
    CONFLICT         = "CONFLICT"
    SUCCESS          = "SUCCESS"
    FAILURE          = "FAILURE"
    AMBIGUOUS        = "AMBIGUOUS"


@dataclass
class ActionOutcome:
    """
    Зафиксированный результат действия продавца / рекомендации Argus.

    metrics_before / metrics_after — произвольные числовые метрики
    (orders, revenue, ctr, …). Никаких выдуманных значений.
    """

    id: str
    user_hash: str
    category: str
    recommendation_type: str
    action: str
    period_start: float
    period_end: float
    created_at: float
    article: str | None = None
    metrics_before: dict = field(default_factory=dict)
    metrics_after: dict = field(default_factory=dict)
    outcome_direction: OutcomeDirection = OutcomeDirection.UNKNOWN
    outcome_score: float = 0.0          # -1.0 .. +1.0
    confidence: float = 0.5
    evidence_ids: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class LearningSignal:
    """Сигнал, извлечённый из одного или нескольких ActionOutcome."""

    id: str
    outcome_id: str | None
    signal_type: LearningSignalType
    claim: str
    confidence: float
    evidence_ids: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    created_at: float = 0.0


@dataclass
class LearningAssessment:
    """
    Агрегированная оценка эффективности действия в категории.

    sample_size / success_count / failure_count — только из реальных outcomes.
    """

    category: str
    action: str | None
    sample_size: int
    success_count: int
    failure_count: int
    success_rate: float
    confidence: float
    signals: list[LearningSignal] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    generated_at: float = 0.0
