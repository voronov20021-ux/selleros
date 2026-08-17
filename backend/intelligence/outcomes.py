"""
outcomes.py — модели Recommendation Outcome Tracker v1.

Жизненный цикл:
    RECOMMENDATION → record_recommendation()
    → продавец решает сам → record_action()
    → проходит время → record_result()
    → outcome_direction → LearningBrain (вручную / позже)

UNKNOWN — нормальное состояние, пока результата нет.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class OutcomeDirection(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    MIXED    = "mixed"
    UNKNOWN  = "unknown"


@dataclass
class RecommendationOutcome:
    """
    Зафиксированная рекомендация Argus и (опционально) её результат.

    До record_result() direction = UNKNOWN, score = None.
    Не содержит raw user_id — только user_hash.
    """

    id: str
    user_hash: str
    category: str
    recommendation_type: str
    recommendation_action: str
    recommendation_confidence: float
    recommended_at: float
    article: str | None = None
    action_taken: str | None = None
    action_taken_at: float | None = None
    period_start: float | None = None
    period_end: float | None = None
    metrics_before: dict = field(default_factory=dict)
    metrics_after: dict = field(default_factory=dict)
    outcome_direction: OutcomeDirection = OutcomeDirection.UNKNOWN
    outcome_score: float | None = None   # -1..+1 или None
    confidence: float = 0.0
    evidence_ids: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class MetricAnalysis:
    """Результат analyze_result() — только из реально переданных метрик."""

    outcome_direction: OutcomeDirection
    outcome_score: float | None
    confidence: float
    comparisons: int = 0
    improved: int = 0
    worsened: int = 0
