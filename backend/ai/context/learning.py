"""
ai/context/learning.py — исторический опыт Learning Loop для Argus.

LearningContextSource:
    product.subject_name → LearningBrain.analyze(category, days=90)
                         → компактный ContextBlock (priority=40)

Только store/database. Без HTTP / SearchService / LLM.
"""

from __future__ import annotations

import logging
import re

from backend.ai.context.base import ContextBlock, ContextRequest, ContextSource
from backend.ai.intents import Intent
from backend.intelligence.learning import LearningSignalType

log = logging.getLogger("selleros.ai.context.learning")

# Intent не должен отключать learning memory: любой product-related intent
# получает тот же исторический опыт (MemoryContext тоже несёт learning).
_RELEVANT_INTENTS = frozenset({
    Intent.COMPETITOR,
    Intent.MARKETING,
    Intent.PRICING,
    Intent.SELLER_ANALYTICS,
    Intent.GENERAL_QUESTION,
    Intent.PRODUCT_DISCUSSION,
    Intent.REVIEWS,
    Intent.LOGISTICS,
    Intent.PHOTO,
})

_MAX_BLOCK_CHARS = 1500
_MAX_PATTERN_LINES = 3
_MAX_CLAIM_LEN = 120

# UUID / длинные hex — не показываем пользователю/LLM
_RE_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.I,
)


def _clean_claim(claim: str) -> str:
    """Убрать технические префиксы и UUID из claim."""
    text = claim or ""
    text = re.sub(r"^\[(FACT|OBSERVATION|INFERENCE)\]\s*", "", text)
    text = _RE_UUID.sub("", text)
    text = re.sub(
        r"\b(evidence_ids?|user_hash|user_id|ActionOutcome|KnowledgeItem)\b[=:]?\s*",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(r"\s{2,}", " ", text).strip(" .;")
    return text[:_MAX_CLAIM_LEN]


def _format_assessment(assessment) -> str:
    """Компактный текст LearningAssessment для LLM. ≤1500 символов."""
    lines: list[str] = []

    cat = assessment.category or "?"
    lines.append(f"Категория: {cat}")
    lines.append(f"Выборка: {assessment.sample_size} действий")
    lines.append(f"Успешность: {assessment.success_rate:.0%}")
    lines.append(
        f"Успехи/неудачи: {assessment.success_count}/{assessment.failure_count}"
    )

    positive = [
        s for s in assessment.signals
        if s.signal_type == LearningSignalType.POSITIVE_PATTERN
    ]
    negative = [
        s for s in assessment.signals
        if s.signal_type == LearningSignalType.NEGATIVE_PATTERN
    ]
    mixed = [
        s for s in assessment.signals
        if s.signal_type == LearningSignalType.MIXED_PATTERN
    ]
    low = [
        s for s in assessment.signals
        if s.signal_type == LearningSignalType.LOW_CONFIDENCE
    ]

    if positive:
        lines.append("Положительные паттерны:")
        for s in positive[:_MAX_PATTERN_LINES]:
            claim = _clean_claim(s.claim)
            if claim:
                lines.append(f"• {claim}")

    if negative:
        lines.append("Отрицательные:")
        for s in negative[:_MAX_PATTERN_LINES]:
            claim = _clean_claim(s.claim)
            if claim:
                lines.append(f"• {claim}")

    if mixed:
        lines.append("Смешанные результаты:")
        for s in mixed[:_MAX_PATTERN_LINES]:
            claim = _clean_claim(s.claim)
            if claim:
                lines.append(f"• {claim}")

    warnings = list(assessment.warnings or [])
    for s in low:
        w = _clean_claim(s.claim)
        if w and w not in warnings:
            warnings.append(w)

    if warnings:
        lines.append("Ограничение:")
        for w in warnings[:_MAX_PATTERN_LINES]:
            lines.append(f"• {_clean_claim(w) if w else w}")

    text = "\n".join(lines)
    if len(text) > _MAX_BLOCK_CHARS:
        text = text[:_MAX_BLOCK_CHARS - 1].rstrip() + "…"
    return text


class LearningContextSource(ContextSource):
    """
    Источник исторического опыта LearningBrain для Argus.

    learning_brain — LearningBrain | None
    session        — SessionService
    """

    name = "learning_experience"
    intents = _RELEVANT_INTENTS

    def __init__(self, learning_brain, session) -> None:
        self._brain = learning_brain
        self._session = session

    async def fetch(self, request: ContextRequest) -> ContextBlock | None:
        if self._brain is None:
            return None

        try:
            product = self._session.get_product(request.user_id)
        except Exception as exc:
            log.warning("LearningContextSource: session error: %s", exc)
            return None

        if product is None:
            return None

        category = getattr(product, "subject_name", None)
        if not category:
            return None

        try:
            assessment = await self._brain.analyze(
                category=category,
                action=None,
                days=90,
            )
        except Exception as exc:
            log.warning(
                "LearningContextSource: analyze(%r) failed: %s",
                category, exc,
            )
            return None

        if assessment is None or assessment.sample_size <= 0:
            return None

        body = _format_assessment(assessment)
        if not body.strip():
            return None

        return ContextBlock(
            title="📚 ИСТОРИЧЕСКИЙ ОПЫТ",
            body=body,
            priority=40,
        )
