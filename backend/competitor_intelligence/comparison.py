"""
CompetitorComparison — seller vs median/range.

1 кандидат ≠ «рынок показывает».
Позиция цены — квартили выборки, не правило «+10% = плохо».
"""

from __future__ import annotations

from statistics import median
from typing import Any, Sequence

from backend.competitor_intelligence.models import (
    MIN_MARKET_SAMPLE,
    PRICE_SPREAD_MAX_RATIO,
    CompetitorComparison,
    CompetitorEvidence,
    EvidenceQuality,
    MetricCompare,
    PricePosition,
    ProductCompetitorProfile,
    SearchCandidate,
    evidence_quality_of,
)


def evidence_from_candidate(cand: SearchCandidate) -> CompetitorEvidence:
    char_count = None
    if cand.characteristics and isinstance(cand.characteristics, dict):
        char_count = len(cand.characteristics)
    ev = CompetitorEvidence(
        competitor_id=cand.competitor_id,
        source=cand.source,
        source_url=cand.source_url,
        title=cand.title,
        brand=cand.brand,
        category=cand.category,
        price=cand.price,
        rating=cand.rating,
        feedbacks=cand.feedbacks,
        discount=cand.discount,
        photo_count=cand.photo_count,
        characteristics=cand.characteristics,
        description=cand.description,
        available_positioning=cand.available_positioning,
        matched_attributes=list(cand.matched_attributes),
        confidence=float(cand.confidence or 0.0),
        retrieved_at=cand.retrieved_at,
        source_timestamp=cand.source_timestamp,
        marketplace=cand.marketplace,
        nm_id=cand.nm_id,
        char_count=char_count,
    )
    ev.refresh_quality()
    return ev


def _nums(values: Sequence[Any]) -> list[float]:
    out: list[float] = []
    for v in values:
        if v is None:
            continue
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            continue
    return out


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def _metric(seller: float | None, peers: list[float], *, min_n: int) -> MetricCompare:
    n = len(peers)
    sufficient = n >= min_n
    if not n:
        return MetricCompare(seller=seller, sample_n=0, sufficient=False)
    lo, hi = min(peers), max(peers)
    med = float(median(peers)) if sufficient else None
    diff = None
    pct = None
    if sufficient and seller is not None and med is not None and med != 0:
        diff = seller - med
        pct = round(diff / med * 100.0, 1)
    return MetricCompare(
        seller=seller,
        competitor_median=med,
        competitor_min=lo,
        competitor_max=hi,
        difference=diff,
        difference_pct=pct,
        sample_n=n,
        sufficient=sufficient,
    )


def _price_position(seller: float | None, peers: list[float], *, sufficient: bool) -> str:
    if seller is None or not sufficient or len(peers) < MIN_MARKET_SAMPLE:
        return PricePosition.UNKNOWN
    ordered = sorted(peers)
    p25 = _percentile(ordered, 0.25)
    p75 = _percentile(ordered, 0.75)
    if seller < p25:
        return PricePosition.BELOW_MARKET
    if seller > p75:
        return PricePosition.ABOVE_MARKET
    return PricePosition.MARKET_RANGE


def _is_inhomogeneous(prices: list[float]) -> bool:
    if len(prices) < 2:
        return False
    lo, hi = min(prices), max(prices)
    if lo <= 0:
        return True
    return (hi / lo) > float(PRICE_SPREAD_MAX_RATIO)


def _honesty_note(
    n: int,
    *,
    price_known_n: int,
    seller_price: float | None,
    inhomogeneous: bool = False,
    commercial_n: int | None = None,
) -> str:
    comm = price_known_n if commercial_n is None else commercial_n
    if n <= 0:
        return "Сопоставимые конкуренты не найдены — рыночное сравнение недоступно."
    if n == 1:
        return "Найден 1 сопоставимый кандидат — недостаточно для рыночного сравнения."
    if n < MIN_MARKET_SAMPLE:
        return (
            f"По {n} сопоставимым товарам данных недостаточно для оценки рынка."
        )
    if inhomogeneous:
        return (
            f"Найдено {n} кандидатов, но выборка слишком неоднородна по цене — "
            "медиану рынка не считаю."
        )
    if price_known_n < MIN_MARKET_SAMPLE:
        return (
            f"Найдено {n} кандидатов, но коммерческие поля подтверждены только у {comm}. "
            "Точную позицию по цене пока не определяю."
        )
    if seller_price is None:
        return "Цена своего товара неизвестна — позиция относительно рынка UNKNOWN."
    return ""


def compare_with_competitors(
    profile: ProductCompetitorProfile,
    selected: Sequence[CompetitorEvidence] | Sequence[SearchCandidate],
    *,
    min_sample: int = MIN_MARKET_SAMPLE,
    search_stats: dict | None = None,
    query: str | None = None,
    discovered_n: int = 0,
) -> CompetitorComparison:
    evidence: list[CompetitorEvidence] = []
    for item in selected or []:
        if isinstance(item, CompetitorEvidence):
            evidence.append(item)
        else:
            evidence.append(evidence_from_candidate(item))

    n = len(evidence)
    prices = _nums([e.price for e in evidence])
    ratings = _nums([e.rating for e in evidence])
    fbs = _nums([e.feedbacks for e in evidence])
    photos = _nums([e.photo_count for e in evidence])
    chars = _nums([e.char_count for e in evidence])

    seller_price = float(profile.price) if profile.price is not None else None
    seller_rating = float(profile.rating) if profile.rating is not None else None
    seller_fb = float(profile.feedbacks) if profile.feedbacks is not None else None
    seller_photos = float(profile.photo_count) if profile.photo_count is not None else None
    seller_chars = float(profile.char_count) if profile.char_count is not None else None

    price_m = _metric(seller_price, prices, min_n=min_sample)
    inhomogeneous = _is_inhomogeneous(prices)
    commercial_n = sum(
        1 for e in evidence
        if e.price is not None or e.rating is not None or e.feedbacks is not None
    )
    qualities = [e.quality or evidence_quality_of(title=e.title, price=e.price, rating=e.rating, feedbacks=e.feedbacks) for e in evidence]
    if qualities and all(q == EvidenceQuality.FULL for q in qualities):
        overall_q = EvidenceQuality.FULL
    elif any(q in (EvidenceQuality.FULL, EvidenceQuality.PARTIAL) for q in qualities):
        overall_q = EvidenceQuality.PARTIAL
    else:
        overall_q = EvidenceQuality.UNKNOWN

    sufficient = (
        n >= min_sample
        and price_m.sufficient
        and not inhomogeneous
    )
    if not sufficient:
        price_m = MetricCompare(
            seller=seller_price,
            competitor_median=None,
            competitor_min=min(prices) if prices else None,
            competitor_max=max(prices) if prices else None,
            difference=None,
            difference_pct=None,
            sample_n=len(prices),
            sufficient=False,
        )
    position = _price_position(seller_price, prices, sufficient=sufficient)
    rating_m = _metric(seller_rating, ratings, min_n=min_sample)
    fb_m = _metric(seller_fb, fbs, min_n=min_sample)
    photo_m = _metric(seller_photos, photos, min_n=min_sample)
    char_m = _metric(seller_chars, chars, min_n=min_sample)
    if not sufficient:
        rating_m = MetricCompare(
            seller=seller_rating,
            competitor_min=min(ratings) if ratings else None,
            competitor_max=max(ratings) if ratings else None,
            sample_n=len(ratings),
            sufficient=False,
        )
        fb_m = MetricCompare(
            seller=seller_fb,
            competitor_min=min(fbs) if fbs else None,
            competitor_max=max(fbs) if fbs else None,
            sample_n=len(fbs),
            sufficient=False,
        )

    return CompetitorComparison(
        seller_article=profile.article,
        sample_n=n,
        comparable_n=n,
        sufficient_for_market=sufficient,
        price=price_m,
        rating=rating_m,
        feedbacks=fb_m,
        photos=photo_m,
        characteristics=char_m,
        price_position=position,
        honesty_note=_honesty_note(
            n,
            price_known_n=len(prices),
            seller_price=seller_price,
            inhomogeneous=inhomogeneous,
            commercial_n=commercial_n,
        ),
        competitors=evidence,
        search_stats=dict(search_stats or {}),
        query=query,
        discovered_n=discovered_n or n,
        commercial_n=commercial_n,
        inhomogeneous=inhomogeneous,
        evidence_quality=overall_q,
    )


def to_advisor_market_dict(cmp: CompetitorComparison | None) -> dict[str, Any] | None:
    if cmp is None:
        return None
    seller = cmp.price.seller
    if cmp.sufficient_for_market and cmp.price.competitor_median is not None and seller is not None:
        med = cmp.price.competitor_median
        pct = cmp.price.difference_pct
        text = (
            f"цена {seller:.0f} ₽ vs медиана сопоставимых {med:.0f} ₽ "
            f"({pct:+.1f}%, n={cmp.price.sample_n})"
        )
        return {
            "our_price": seller,
            "median": med,
            "pct_vs_median": pct,
            "peer_count": cmp.sample_n,
            "sufficient": True,
            "price_position": cmp.price_position,
            "range_min": cmp.price.competitor_min,
            "range_max": cmp.price.competitor_max,
            "text": text,
        }
    note = cmp.honesty_note or "недостаточно данных для рыночного сравнения"
    return {
        "our_price": seller,
        "median": None,
        "pct_vs_median": None,
        "peer_count": cmp.sample_n,
        "sufficient": False,
        "price_position": PricePosition.UNKNOWN,
        "text": note,
    }


def _fmt_price(value: float) -> str:
    return f"{int(round(value)):,}".replace(",", " ")


def format_market_block(cmp: CompetitorComparison | None) -> str:
    """Пользовательский блок 🏪 РЫНОК. Без competitor_id / ranking scores."""
    if cmp is None:
        return "🏪 РЫНОК\nДанных о сопоставимых конкурентах нет."
    lines = ["🏪 РЫНОК"]
    if not cmp.sufficient_for_market:
        lines.append(cmp.honesty_note or "недостаточно данных для рыночного сравнения")
        if cmp.sample_n == 1 and cmp.competitors:
            c = cmp.competitors[0]
            bits = []
            if c.price is not None:
                bits.append(f"цена кандидата {c.price} ₽")
            if c.rating is not None:
                bits.append(f"рейтинг {c.rating}")
            if bits:
                lines.append("Наблюдение (не рынок): " + ", ".join(bits) + ".")
        return "\n".join(lines)

    p = cmp.price
    if p.competitor_min is not None and p.competitor_max is not None:
        lines.append(
            f"Цена конкурентов: {_fmt_price(p.competitor_min)}–{_fmt_price(p.competitor_max)} ₽"
        )
    lines.append(f"Сопоставимых товаров: {cmp.sample_n}")
    r = cmp.rating
    if r.competitor_min is not None and r.competitor_max is not None:
        lines.append(
            f"Рейтинг конкурентов: {r.competitor_min:g}–{r.competitor_max:g}"
        )
    f = cmp.feedbacks
    if f.sufficient and f.seller is not None and f.competitor_median is not None:
        lines.append(
            f"Отзывы: ваши {f.seller:.0f} vs медиана {f.competitor_median:.0f}"
        )
    pos_map = {
        PricePosition.BELOW_MARKET: "ниже типичного диапазона выборки",
        PricePosition.MARKET_RANGE: "в диапазоне рынка (по доступной выборке)",
        PricePosition.ABOVE_MARKET: "выше типичного диапазона выборки",
        PricePosition.UNKNOWN: "неизвестно",
    }
    lines.append(f"Позиция товара: {pos_map.get(cmp.price_position, 'неизвестно')}")
    return "\n".join(lines)
