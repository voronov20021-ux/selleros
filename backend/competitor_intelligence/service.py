"""
ArgusCompetitorIntelligence — SearchService path для ARGUS reasoning.

Цепочка:
  Product → profile → SearchService → candidates → ranking
  → evidence → comparison → diagnosis → Advisor
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable

from backend.ai.finance_planner import ProductCandidate, resolve_product
from backend.competitor_intelligence.comparison import (
    format_market_block,
    to_advisor_market_dict,
)
from backend.competitor_intelligence.models import (
    DEFAULT_ENRICH_N,
    CompetitorComparison,
    CompetitiveDiagnosis,
    DiscoveryResult,
    ProductCompetitorProfile,
)
from backend.competitor_intelligence.profile import build_competitor_profile
from backend.competitor_intelligence.reasoning import diagnose_competitive
from backend.competitor_intelligence.search_collector import SearchCompetitorCollector

log = logging.getLogger("selleros.competitor_intelligence.argus")


@dataclass
class CompetitorTurnResult:
    text: str
    discovery: DiscoveryResult | None = None
    comparison: CompetitorComparison | None = None
    diagnosis: CompetitiveDiagnosis | None = None
    clarify: str | None = None
    product: Any = None


class ArgusCompetitorIntelligence:
    def __init__(
        self,
        search_service: Any = None,
        *,
        intel_store: Any = None,
        top_n: int = DEFAULT_ENRICH_N,
        public_cache: Any = None,
        detail_fetcher: Any = None,
    ) -> None:
        self.search_service = search_service
        self.intel_store = intel_store or getattr(search_service, "_store", None)
        self.collector = SearchCompetitorCollector(
            search_service,
            intel_store=self.intel_store,
            top_n=top_n,
            public_cache=public_cache,
            detail_fetcher=detail_fetcher,
        )

    async def analyze_product(
        self,
        product: Any,
        *,
        seller_data=None,
        review_assessment=None,
        funnel_diag=None,
        unit_econ: dict | None = None,
        top_n: int | None = None,
        card_healthy: bool = False,
    ) -> DiscoveryResult:
        profile = build_competitor_profile(product)
        if seller_data is not None:
            if profile.price is None and getattr(seller_data, "price", None) is not None:
                try:
                    profile.price = int(seller_data.price)
                except (TypeError, ValueError):
                    pass
        discovery = await self.collector.discover(profile, top_n=top_n)
        comparison = discovery.comparison
        diagnosis = diagnose_competitive(
            comparison,
            review_assessment=review_assessment,
            funnel_diag=funnel_diag,
            unit_econ=unit_econ,
            seller_data=seller_data,
            card_healthy=card_healthy,
        )
        discovery.diagnosis = diagnosis
        return discovery


def format_competitor_human_reply(
    *,
    product: Any,
    comparison: CompetitorComparison | None,
    diagnosis: CompetitiveDiagnosis | None,
    advisor_text: str | None = None,
) -> str:
    """
    Человеческий ответ. Без competitor_id / ranking / parser debug.
    Если есть advisor_text — он уже содержит секции ARGUS; дополняем РЫНОК,
    если его ещё нет.
    """
    if advisor_text and str(advisor_text).strip():
        text = str(advisor_text).strip()
        if "🏪 РЫНОК" not in text:
            text = text + "\n\n" + format_market_block(comparison)
        if diagnosis and diagnosis.insight and diagnosis.insight not in text:
            pass
        return text

    lines: list[str] = []
    kind = (diagnosis.kind if diagnosis else "") or ""
    if kind == "no_action":
        lines.append("🎯 ГЛАВНАЯ ПРОБЛЕМА")
        lines.append("Системной проблемы пока не видно.")
    elif diagnosis and diagnosis.insight:
        lines.append("🎯 ГЛАВНАЯ ПРОБЛЕМА")
        lines.append(diagnosis.insight)
    else:
        lines.append("🎯 ГЛАВНАЯ ПРОБЛЕМА")
        lines.append("Данных для жёсткого конкурентного вывода пока мало.")

    price = getattr(product, "price", None) if product is not None else None
    rating = getattr(product, "rating", None) if product is not None else None
    feedbacks = getattr(product, "feedbacks", None) if product is not None else None
    lines.append("")
    lines.append("📊 КЛЮЧЕВЫЕ ЦИФРЫ")
    lines.append(f"Цена: {price} ₽" if price is not None else "Цена: нет данных")
    lines.append(f"Рейтинг: {rating}" if rating is not None else "Рейтинг: нет данных")
    lines.append(f"Отзывы: {feedbacks}" if feedbacks is not None else "Отзывы: нет данных")
    lines.append("")
    lines.append(format_market_block(comparison))
    lines.append("")
    lines.append("🔧 ЧТО ДЕЛАТЬ")
    if diagnosis and diagnosis.action_class == "NO_ACTION":
        lines.append("1. Ничего критичного не менять — мониторить рейтинг и экономику")
    elif diagnosis and diagnosis.action_hint:
        lines.append(f"1. {diagnosis.action_hint}")
    else:
        lines.append("1. Собрать больше сопоставимых конкурентов / CTR/CVR перед выводом")
    lines.append("")
    lines.append("🚫 ЧТО НЕ ТРОГАТЬ")
    nrs = list((diagnosis.not_recommended if diagnosis else []) or [])
    if not nrs:
        nrs = ["не утверждать продажи конкурентов — таких данных нет"]
    for i, nr in enumerate(nrs[:4], 1):
        lines.append(f"{i}. {nr}")
    blob = "\n".join(lines)
    for banned in ("competitor_id", "score_parts", "raw_snippet"):
        blob = blob.replace(banned, "")
    return blob


async def handle_competitor_turn(
    text: str,
    *,
    product: Any = None,
    candidates: Iterable[ProductCandidate] | None = None,
    current: ProductCandidate | None = None,
    search_service=None,
    intel_store=None,
    seller_data=None,
    review_assessment=None,
    unit_econ: dict | None = None,
    cached: DiscoveryResult | CompetitorComparison | None = None,
    card_healthy: bool = False,
    public_cache=None,
    detail_fetcher=None,
) -> CompetitorTurnResult:
    cands = list(candidates or [])
    resolved, ambiguous, clarify = resolve_product(text, cands, current=current)
    if clarify and ambiguous:
        return CompetitorTurnResult(text=clarify, clarify=clarify)

    use_product = product
    if resolved is not None and product is not None:
        art = getattr(product, "article", None)
        if art is not None and int(resolved.article) != int(art):
            use_product = product
    if use_product is None and resolved is not None:
        use_product = resolved

    svc = ArgusCompetitorIntelligence(
        search_service,
        intel_store=intel_store,
        public_cache=public_cache,
        detail_fetcher=detail_fetcher,
    )
    discovery: DiscoveryResult | None = None
    comparison: CompetitorComparison | None = None
    if isinstance(cached, DiscoveryResult):
        discovery = cached
        comparison = cached.comparison
    elif isinstance(cached, CompetitorComparison):
        comparison = cached
    elif isinstance(cached, dict) and cached.get("sample_n") is not None:
        try:
            from backend.ai.advisor import _comparison_from_meta
            comparison = _comparison_from_meta(cached)
        except Exception:
            comparison = None
    if discovery is None and comparison is None and use_product is not None:
        discovery = await svc.analyze_product(
            use_product,
            seller_data=seller_data,
            review_assessment=review_assessment,
            unit_econ=unit_econ,
            card_healthy=card_healthy,
        )
        comparison = discovery.comparison if discovery else None
    diagnosis = discovery.diagnosis if discovery else None
    if diagnosis is None and comparison is not None:
        diagnosis = diagnose_competitive(
            comparison,
            review_assessment=review_assessment,
            unit_econ=unit_econ,
            seller_data=seller_data,
            card_healthy=card_healthy,
        )
    text_out = format_competitor_human_reply(
        product=use_product,
        comparison=comparison,
        diagnosis=diagnosis,
    )
    return CompetitorTurnResult(
        text=text_out,
        discovery=discovery,
        comparison=comparison,
        diagnosis=diagnosis,
        product=use_product,
    )
