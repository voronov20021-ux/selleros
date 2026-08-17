"""
ai/context/reasoner.py — Intelligence Reasoner источник для Argus.

ReasonerContextSource:
    product.subject_name → IntelligenceCatalog.search()
                         → IntelligenceReasoner.reason()
                         → компактный ContextBlock (priority=35)

Не дублирует CategoryIntelligenceSource (priority=30, сырые факты).
Этот источник передаёт только сигналы и выводы рассуждателя.
"""

from __future__ import annotations

import logging

from backend.ai.context.base import ContextBlock, ContextRequest, ContextSource
from backend.ai.intents import Intent

log = logging.getLogger("selleros.ai.context.reasoner")

_RELEVANT_INTENTS = frozenset({
    Intent.COMPETITOR,
    Intent.MARKETING,
    Intent.PRICING,
    Intent.SELLER_ANALYTICS,
    Intent.GENERAL_QUESTION,
    Intent.PRODUCT_DISCUSSION,
})

_MAX_BLOCK_CHARS = 1500
_MAX_CONCLUSIONS = 5
_MAX_CLAIM_LEN = 140


def _format_assessment(assessment) -> str:
    """Компактный текст для LLM из IntelligenceAssessment. ≤1500 символов."""
    lines: list[str] = []

    cat = assessment.category or "?"
    lines.append(f"Категория: {cat}")
    lines.append(f"Спрос: {assessment.demand_signal.value}")
    lines.append(f"Тренд: {assessment.trend_signal.value}")
    lines.append(f"Сезонность: {assessment.seasonality_signal.value}")
    lines.append(f"Событие: {assessment.event_signal.value}")
    lines.append(f"Уверенность: {assessment.overall_confidence:.0%}")

    if assessment.opportunities:
        lines.append("Возможности:")
        for c in assessment.opportunities[:_MAX_CONCLUSIONS]:
            lines.append(f"  • {c.claim[:_MAX_CLAIM_LEN]}")

    if assessment.risks:
        lines.append("Риски:")
        for c in assessment.risks[:_MAX_CONCLUSIONS]:
            lines.append(f"  • {c.claim[:_MAX_CLAIM_LEN]}")

    # Остальные выводы (MONITOR и пр.), если ещё есть место
    other = [
        c for c in assessment.conclusions
        if c not in assessment.opportunities and c not in assessment.risks
    ]
    if other:
        lines.append("Наблюдения:")
        for c in other[:_MAX_CONCLUSIONS]:
            lines.append(f"  • {c.claim[:_MAX_CLAIM_LEN]}")

    text = "\n".join(lines)
    if len(text) > _MAX_BLOCK_CHARS:
        text = text[:_MAX_BLOCK_CHARS - 1].rstrip() + "…"
    return text


class ReasonerContextSource(ContextSource):
    """
    Источник выводов IntelligenceReasoner для Argus.

    catalog  — IntelligenceCatalog | None
    reasoner — IntelligenceReasoner | None
    session  — SessionService
    """

    name = "intelligence_reasoner"
    intents = _RELEVANT_INTENTS

    def __init__(self, catalog, reasoner, session) -> None:
        self._catalog = catalog
        self._reasoner = reasoner
        self._session = session

    async def fetch(self, request: ContextRequest) -> ContextBlock | None:
        if self._catalog is None or self._reasoner is None:
            return None

        try:
            product = self._session.get_product(request.user_id)
        except Exception as exc:
            log.warning("ReasonerContextSource: session error: %s", exc)
            return None

        if product is None:
            return None

        category = getattr(product, "subject_name", None)
        if not category:
            return None

        try:
            snapshot = await self._catalog.search(
                category=category,
                region="RU",
                days=30,
                min_confidence=0.40,
                limit=20,
            )
        except Exception as exc:
            log.warning(
                "ReasonerContextSource: catalog.search(%r) failed: %s",
                category, exc,
            )
            return None

        try:
            assessment = await self._reasoner.reason(snapshot)
        except Exception as exc:
            log.warning(
                "ReasonerContextSource: reasoner.reason(%r) failed: %s",
                category, exc,
            )
            return None

        # Пустая оценка без сигналов — не добавляем блок
        if (
            assessment.demand_signal.value == "UNKNOWN"
            and assessment.trend_signal.value == "UNKNOWN"
            and assessment.seasonality_signal.value == "UNKNOWN"
            and assessment.event_signal.value == "NONE"
            and not assessment.conclusions
        ):
            return None

        body = _format_assessment(assessment)
        if not body.strip():
            return None

        title = (
            f"ВЫВОДЫ ARGUS: {category} (RU)"
            f" • confidence {assessment.overall_confidence:.0%}"
        )

        return ContextBlock(
            title=title,
            body=body,
            priority=35,  # ниже market_intelligence (30), выше history (40)
        )
