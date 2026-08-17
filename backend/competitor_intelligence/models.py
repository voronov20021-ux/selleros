"""
Модели Competitor Intelligence (MVP).

Числовые поля без данных = None (не подставляем фейковые 0).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MainProductSnapshot:
    article: int
    title: str | None = None
    price: int | None = None
    rating: float | None = None
    feedbacks: int | None = None
    category_id: int | None = None
    category_name: str | None = None
    brand: str | None = None
    photo_count: int | None = None
    has_description: bool | None = None


@dataclass
class CompetitorCandidate:
    """Кандидат из search.wb.ru до матчинга / обогащения."""

    article: int
    title: str | None = None
    price: int | None = None
    rating: float | None = None
    feedbacks: int | None = None
    category_id: int | None = None
    category_name: str | None = None
    brand: str | None = None
    photo_count: int | None = None
    query: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)
    score: float = 0.0
    score_parts: dict[str, float] = field(default_factory=dict)


@dataclass
class CompetitorProduct:
    """Обогащённый конкурент для CompetitorContext."""

    article: int
    title: str | None = None
    price: int | None = None
    rating: float | None = None
    feedbacks: int | None = None
    photos: list[str] = field(default_factory=list)
    photo_count: int | None = None
    description: str | None = None
    brand: str | None = None
    category_id: int | None = None
    category_name: str | None = None
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    match_score: float | None = None
    sources: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "article": self.article,
            "title": self.title,
            "price": self.price,
            "rating": self.rating,
            "feedbacks": self.feedbacks,
            "photos": list(self.photos),
            "photo_count": self.photo_count,
            "description": self.description,
            "brand": self.brand,
            "category_id": self.category_id,
            "category_name": self.category_name,
            "strengths": list(self.strengths),
            "weaknesses": list(self.weaknesses),
            "match_score": self.match_score,
        }


@dataclass
class CompetitorAnalysis:
    market_position: str = "средний"
    advantages: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    differences: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "market_position": self.market_position,
            "advantages": list(self.advantages),
            "problems": list(self.problems),
            "recommendations": list(self.recommendations),
            "differences": list(self.differences),
        }


# ── ARGUS SearchService evidence layer (не путать с WB search.wb.ru) ──

UNKNOWN = "UNKNOWN"

#: Медиана / «рынок показывает» только при достаточном числе сопоставимых.
MIN_MARKET_SAMPLE = 3

#: Сколько конкурентов оставлять после ranking (configurable).
DEFAULT_TOP_N = 8

#: Enrichment только для top-N отобранных (не для всех 10–30).
DEFAULT_ENRICH_N = 5

#: max/min цены выше этого → выборка неоднородна, медиану не считаем.
PRICE_SPREAD_MAX_RATIO = 3.5


class EvidenceQuality:
    FULL = "FULL"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


def evidence_quality_of(
    *,
    title: str | None,
    price: Any = None,
    rating: Any = None,
    feedbacks: Any = None,
) -> str:
    """FULL = price+rating+reviews+title; PARTIAL = есть коммерческое поле; иначе UNKNOWN."""
    has_title = bool(title and str(title).strip())
    has_price = price is not None
    has_rating = rating is not None
    has_fb = feedbacks is not None
    if has_title and has_price and has_rating and has_fb:
        return EvidenceQuality.FULL
    if has_price or has_rating or has_fb:
        return EvidenceQuality.PARTIAL
    return EvidenceQuality.UNKNOWN


def field_record(
    value: Any,
    *,
    source: str | None = None,
    source_timestamp: float | None = None,
) -> dict[str, Any]:
    present = value is not None and value != UNKNOWN
    return {
        "value": value if present else UNKNOWN,
        "source": source if present and source else UNKNOWN,
        "source_timestamp": source_timestamp if present else None,
        "quality": EvidenceQuality.FULL if present else EvidenceQuality.UNKNOWN,
    }


class PricePosition:
    BELOW_MARKET = "BELOW_MARKET"
    MARKET_RANGE = "MARKET_RANGE"
    ABOVE_MARKET = "ABOVE_MARKET"
    UNKNOWN = "UNKNOWN"


@dataclass
class ProductCompetitorProfile:
    """Профиль поиска только из карточки. Не выдумывает характеристики."""

    article: int | None = None
    title: str | None = None
    brand: str | None = None
    category: str | None = None
    subcategory: str | None = None
    key_characteristics: dict[str, Any] = field(default_factory=dict)
    price: int | None = None
    rating: float | None = None
    feedbacks: int | None = None
    photo_count: int | None = None
    keywords: list[str] = field(default_factory=list)
    product_type: str | None = None
    positioning: str | None = None
    char_count: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "article": self.article,
            "title": self.title,
            "brand": self.brand,
            "category": self.category,
            "subcategory": self.subcategory,
            "key_characteristics": dict(self.key_characteristics),
            "price": self.price,
            "rating": self.rating,
            "feedbacks": self.feedbacks,
            "photo_count": self.photo_count,
            "keywords": list(self.keywords),
            "product_type": self.product_type,
            "positioning": self.positioning,
            "char_count": self.char_count,
        }


@dataclass
class SearchCandidate:
    """Кандидат из SearchService / Yandex до ranking."""

    competitor_id: str
    source: str = "yandex_search"
    source_url: str = ""
    title: str | None = None
    brand: str | None = None
    category: str | None = None
    price: int | None = None
    rating: float | None = None
    feedbacks: int | None = None
    discount: int | None = None
    photo_count: int | None = None
    characteristics: dict[str, Any] | None = None
    description: str | None = None
    available_positioning: str | None = None
    matched_attributes: list[str] = field(default_factory=list)
    confidence: float = 0.0
    retrieved_at: float = 0.0
    source_timestamp: float | None = None
    marketplace: str | None = None
    nm_id: int | None = None
    snippet: str | None = None
    score: float = 0.0
    score_parts: dict[str, float] = field(default_factory=dict)
    reject_reason: str | None = None


@dataclass
class CompetitorEvidence:
    """Структурированное evidence. Отсутствующее поле = None (UX: UNKNOWN)."""

    competitor_id: str
    source: str
    source_url: str
    title: str | None = None
    brand: str | None = None
    category: str | None = None
    price: int | None = None
    rating: float | None = None
    feedbacks: int | None = None
    discount: int | None = None
    photo_count: int | None = None
    characteristics: dict[str, Any] | None = None
    description: str | None = None
    available_positioning: str | None = None
    matched_attributes: list[str] = field(default_factory=list)
    confidence: float = 0.0
    retrieved_at: float = 0.0
    source_timestamp: float | None = None
    marketplace: str | None = None
    nm_id: int | None = None
    char_count: int | None = None
    old_price: int | None = None
    quality: str = EvidenceQuality.UNKNOWN
    fields: dict[str, Any] = field(default_factory=dict)
    enriched_at: float | None = None
    enrichment_source: str | None = None

    def refresh_quality(self) -> str:
        self.quality = evidence_quality_of(
            title=self.title,
            price=self.price,
            rating=self.rating,
            feedbacks=self.feedbacks,
        )
        return self.quality

    def unknown_map(self) -> dict[str, Any]:
        """Сериализация: None → UNKNOWN. Без ranking scores."""
        def _u(v):
            return UNKNOWN if v is None else v

        if not self.quality or self.quality == EvidenceQuality.UNKNOWN:
            self.refresh_quality()

        return {
            "competitor_id": self.competitor_id,
            "source": self.source,
            "source_url": self.source_url,
            "title": _u(self.title),
            "brand": _u(self.brand),
            "category": _u(self.category),
            "price": _u(self.price),
            "old_price": _u(self.old_price),
            "rating": _u(self.rating),
            "feedbacks": _u(self.feedbacks),
            "discount": _u(self.discount),
            "photo_count": _u(self.photo_count),
            "characteristics": _u(self.characteristics),
            "description": _u(self.description),
            "available_positioning": _u(self.available_positioning),
            "matched_attributes": list(self.matched_attributes),
            "confidence": self.confidence,
            "retrieved_at": self.retrieved_at,
            "source_timestamp": _u(self.source_timestamp),
            "marketplace": _u(self.marketplace),
            "nm_id": _u(self.nm_id),
            "char_count": _u(self.char_count),
            "quality": self.quality or EvidenceQuality.UNKNOWN,
            "fields": dict(self.fields or {}),
            "enriched_at": _u(self.enriched_at),
            "enrichment_source": _u(self.enrichment_source),
        }

    def as_dict(self) -> dict[str, Any]:
        return self.unknown_map()


@dataclass
class MetricCompare:
    seller: float | None = None
    competitor_median: float | None = None
    competitor_min: float | None = None
    competitor_max: float | None = None
    difference: float | None = None
    difference_pct: float | None = None
    sample_n: int = 0
    sufficient: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "seller": self.seller,
            "competitor_median": self.competitor_median if self.sufficient else None,
            "competitor_min": self.competitor_min if self.sample_n else None,
            "competitor_max": self.competitor_max if self.sample_n else None,
            "difference": self.difference if self.sufficient else None,
            "difference_pct": self.difference_pct if self.sufficient else None,
            "sample_n": self.sample_n,
            "sufficient": self.sufficient,
        }


@dataclass
class CompetitorComparison:
    seller_article: int | None = None
    sample_n: int = 0
    comparable_n: int = 0
    sufficient_for_market: bool = False
    price: MetricCompare = field(default_factory=MetricCompare)
    rating: MetricCompare = field(default_factory=MetricCompare)
    feedbacks: MetricCompare = field(default_factory=MetricCompare)
    photos: MetricCompare = field(default_factory=MetricCompare)
    characteristics: MetricCompare = field(default_factory=MetricCompare)
    price_position: str = PricePosition.UNKNOWN
    honesty_note: str = ""
    competitors: list[CompetitorEvidence] = field(default_factory=list)
    search_stats: dict[str, Any] = field(default_factory=dict)
    query: str | None = None
    discovered_n: int = 0
    commercial_n: int = 0
    inhomogeneous: bool = False
    evidence_quality: str = EvidenceQuality.UNKNOWN

    def as_dict(self) -> dict[str, Any]:
        return {
            "seller_article": self.seller_article,
            "sample_n": self.sample_n,
            "comparable_n": self.comparable_n,
            "sufficient_for_market": self.sufficient_for_market,
            "price": self.price.as_dict(),
            "rating": self.rating.as_dict(),
            "feedbacks": self.feedbacks.as_dict(),
            "photos": self.photos.as_dict(),
            "characteristics": self.characteristics.as_dict(),
            "price_position": self.price_position,
            "honesty_note": self.honesty_note,
            "competitors": [c.as_dict() for c in self.competitors],
            "search_stats": dict(self.search_stats),
            "query": self.query,
            "discovered_n": self.discovered_n,
            "commercial_n": self.commercial_n,
            "inhomogeneous": self.inhomogeneous,
            "evidence_quality": self.evidence_quality,
        }


@dataclass
class CompetitiveDiagnosis:
    """Подсказка для ARGUS reasoning. Не отдельный генератор рекомендаций."""

    kind: str = "unknown"
    layer: str = "OBSERVATION"  # FACT | OBSERVATION | HYPOTHESIS | CHECK
    insight: str = ""
    action_hint: str | None = None
    not_recommended: list[str] = field(default_factory=list)
    do_not_cut_price: bool = False
    price_position: str = PricePosition.UNKNOWN
    confidence: float = 0.0
    facts: list[str] = field(default_factory=list)
    action_class: str = "CHECK"

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "layer": self.layer,
            "insight": self.insight,
            "action_hint": self.action_hint,
            "not_recommended": list(self.not_recommended),
            "do_not_cut_price": self.do_not_cut_price,
            "price_position": self.price_position,
            "confidence": self.confidence,
            "facts": list(self.facts),
            "action_class": self.action_class,
        }


@dataclass
class CompetitorSnapshot:
    """Заготовка истории. MVP не строит полноценный tracking."""

    competitor_id: str
    captured_at: float
    price: int | None = None
    rating: float | None = None
    feedbacks: int | None = None


@dataclass
class DiscoveryResult:
    query: str
    profile: ProductCompetitorProfile
    raw_items_n: int = 0
    candidates: list[SearchCandidate] = field(default_factory=list)
    selected: list[CompetitorEvidence] = field(default_factory=list)
    comparison: CompetitorComparison | None = None
    diagnosis: CompetitiveDiagnosis | None = None
    search_http_calls: int = 0
    search_cache_hits: int = 0
    cost_guard_status: str | None = None
    browser_calls: int = 0
    from_competitor_cache: bool = False
    clarify: str | None = None
    enrich_http_calls: int = 0
    enrich_cache_hits: int = 0
    enriched_n: int = 0
