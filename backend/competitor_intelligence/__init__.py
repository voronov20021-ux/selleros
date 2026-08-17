"""
backend.competitor_intelligence — слой конкурентов для Argus (MVP).

Использование::

    from backend.competitor_intelligence import CompetitorIntelligence

    ci = CompetitorIntelligence(
        product_builder=builder,   # ProductContextBuilder
        proxy_pool=proxy_pool,
    )
    product_ctx, competitor_ctx = await ci.build(article)
    prompt = competitor_ctx.to_prompt()

Тонкий хук для prompt-сборки (не меняет reasoner)::

    from backend.competitor_intelligence import attach_competitor_context_to_prompt
    full = attach_competitor_context_to_prompt(existing_prompt, competitor_ctx)
"""

from backend.competitor_intelligence.analyzer import CompetitorAnalyzer
from backend.competitor_intelligence.collector import CompetitorCollector
from backend.competitor_intelligence.comparison import (
    compare_with_competitors,
    format_market_block,
    to_advisor_market_dict,
)
from backend.competitor_intelligence.context import (
    CompetitorContext,
    CompetitorIntelligence,
    main_from_product_context,
)
from backend.competitor_intelligence.matcher import CompetitorMatcher
from backend.competitor_intelligence.models import (
    CompetitorAnalysis,
    CompetitorCandidate,
    CompetitorComparison,
    CompetitorEvidence,
    CompetitorProduct,
    CompetitiveDiagnosis,
    MainProductSnapshot,
    ProductCompetitorProfile,
    PricePosition,
)
from backend.competitor_intelligence.profile import build_competitor_profile
from backend.competitor_intelligence.ranking import rank_candidates
from backend.competitor_intelligence.reasoning import diagnose_competitive
from backend.competitor_intelligence.service import (
    ArgusCompetitorIntelligence,
    format_competitor_human_reply,
    handle_competitor_turn,
)


def attach_competitor_context_to_prompt(
    base_prompt: str | None,
    context: CompetitorContext | None,
    *,
    separator: str = "\n\n",
) -> str:
    """
    Безопасно дописать context.to_prompt() к уже собранному prompt.

    Не трогает analyzer/brain/reasoner — только конкатенация строк.
    """
    base = (base_prompt or "").rstrip()
    if context is None:
        return base
    block = context.to_prompt().strip()
    if not block:
        return base
    if not base:
        return block
    return f"{base}{separator}{block}"


__all__ = [
    "MainProductSnapshot",
    "CompetitorCandidate",
    "CompetitorProduct",
    "CompetitorAnalysis",
    "CompetitorContext",
    "CompetitorCollector",
    "CompetitorMatcher",
    "CompetitorAnalyzer",
    "CompetitorIntelligence",
    "CompetitorComparison",
    "CompetitorEvidence",
    "CompetitiveDiagnosis",
    "ProductCompetitorProfile",
    "PricePosition",
    "ArgusCompetitorIntelligence",
    "build_competitor_profile",
    "rank_candidates",
    "compare_with_competitors",
    "diagnose_competitive",
    "format_market_block",
    "to_advisor_market_dict",
    "format_competitor_human_reply",
    "handle_competitor_turn",
    "main_from_product_context",
    "attach_competitor_context_to_prompt",
]
