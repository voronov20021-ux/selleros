"""
learning_brain.py — оркестратор Learning Loop v1.

LearningBrain:
    record_outcome(outcome)  — сохранить ActionOutcome
    analyze(category, …)     — загрузить outcomes → LearningAssessment
    build_signals(outcomes)  — извлечь LearningSignal
    assess(outcomes)         — делегировать в LearningBrainProvider

На v1: только rule-based (RuleBasedLearningBrainProvider).
Не подключён к ContextBuilder / Argus.
Без HTTP / LLM.
"""

from __future__ import annotations

import logging
import time

from backend.intelligence.interfaces import IIntelligenceStore
from backend.intelligence.learning import (
    ActionOutcome,
    LearningAssessment,
    LearningSignal,
)
from backend.intelligence.learning_brain_interface import (
    LearningBrainProvider,
    RuleBasedLearningBrainProvider,
)

log = logging.getLogger("selleros.intelligence.learning_brain")


class LearningBrain:
    """
    Фундамент памяти результатов действий.

    provider — LearningBrainProvider (по умолчанию RuleBased).
    В будущем можно подставить ML/LLM-провайдер без смены Argus.
    """

    def __init__(
        self,
        store: IIntelligenceStore,
        provider: LearningBrainProvider | None = None,
    ) -> None:
        self._store = store
        self._provider = provider or RuleBasedLearningBrainProvider()

    async def record_outcome(self, outcome: ActionOutcome) -> None:
        """Сохранить ActionOutcome в store (idempotent по id)."""
        await self._store.save_action_outcome(outcome)
        log.info(
            "LearningBrain.record_outcome: %s/%s dir=%s score=%.2f",
            outcome.category, outcome.action,
            outcome.outcome_direction.value, outcome.outcome_score,
        )

    async def record_signal(self, signal: LearningSignal) -> LearningSignal:
        """
        Сохранить LearningSignal.

        Если metadata.source_outcome_id уже есть — вернуть существующий
        (idempotency, без дубля).
        """
        source_id = (signal.metadata or {}).get("source_outcome_id")
        if source_id:
            existing = await self.find_signal_by_source_outcome(str(source_id))
            if existing is not None:
                return existing

        await self._store.save_learning_signal(signal)
        log.info(
            "LearningBrain.record_signal: %s conf=%.2f",
            signal.signal_type.value, signal.confidence,
        )
        return signal

    async def find_signal_by_source_outcome(
        self,
        source_outcome_id: str,
    ) -> LearningSignal | None:
        """Найти LearningSignal по metadata.source_outcome_id."""
        if not source_outcome_id:
            return None
        # Предпочитаем store-метод, если есть; иначе — безопасный fallback.
        finder = getattr(self._store, "find_learning_signal_by_source_outcome", None)
        if callable(finder):
            return await finder(source_outcome_id)

        signals = await self._store.search_learning_signals(limit=500)
        for sig in signals:
            if (sig.metadata or {}).get("source_outcome_id") == source_outcome_id:
                return sig
        return None

    async def analyze(
        self,
        category: str,
        action: str | None = None,
        days: int = 90,
    ) -> LearningAssessment:
        """
        Загрузить outcomes за период и построить LearningAssessment.
        """
        since_ts = time.time() - days * 86400 if days > 0 else None
        outcomes = await self._store.search_action_outcomes(
            category=category,
            action=action,
            since_ts=since_ts,
            limit=500,
        )
        assessment = await self.assess(outcomes)

        # Persist derived signals (best-effort)
        for sig in assessment.signals:
            try:
                await self._store.save_learning_signal(sig)
            except Exception as exc:
                log.warning("LearningBrain: не удалось сохранить signal: %s", exc)

        return assessment

    async def build_signals(
        self,
        outcomes: list[ActionOutcome],
    ) -> list[LearningSignal]:
        """Извлечь LearningSignal из outcomes через provider."""
        assessment = await self.assess(outcomes)
        return list(assessment.signals)

    async def assess(
        self,
        outcomes: list[ActionOutcome],
    ) -> LearningAssessment:
        """Делегировать анализ в LearningBrainProvider."""
        return await self._provider.analyze(outcomes)
