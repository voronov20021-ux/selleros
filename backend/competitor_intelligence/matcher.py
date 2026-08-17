"""
CompetitorMatcher — простой скоринг похожести без ML/embeddings.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from backend.competitor_intelligence.models import CompetitorCandidate

_STOPWORDS = frozenset({
    "для", "и", "или", "на", "в", "с", "со", "по", "от", "из", "к", "ко",
    "а", "но", "the", "and", "or", "of", "to", "a", "an", "шт", "см", "мм",
})


def _tokens(text: str | None) -> set[str]:
    if not text:
        return set()
    parts = re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", str(text).lower())
    return {p for p in parts if len(p) >= 2 and p not in _STOPWORDS}


def title_similarity(a: str | None, b: str | None) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        # fallback на SequenceMatcher по сырой строке
        sa = (a or "").strip().lower()
        sb = (b or "").strip().lower()
        if not sa or not sb:
            return 0.0
        return SequenceMatcher(None, sa, sb).ratio()
    jaccard = len(ta & tb) / max(1, len(ta | tb))
    seq = SequenceMatcher(
        None,
        " ".join(sorted(ta)),
        " ".join(sorted(tb)),
    ).ratio()
    return 0.65 * jaccard + 0.35 * seq


def price_similarity(main: int | None, other: int | None) -> float:
    if main is None or other is None or main <= 0 or other <= 0:
        return 0.35  # нейтрально, данных нет
    ratio = min(main, other) / max(main, other)
    # внутри ±40% почти полный балл
    if ratio >= 0.6:
        return ratio
    return max(0.0, ratio * 0.5)


def rating_similarity(main: float | None, other: float | None) -> float:
    if main is None or other is None:
        return 0.35
    diff = abs(float(main) - float(other))
    return max(0.0, 1.0 - diff / 5.0)


def feedbacks_similarity(main: int | None, other: int | None) -> float:
    if main is None or other is None:
        return 0.35
    a, b = max(0, int(main)), max(0, int(other))
    if a == 0 and b == 0:
        return 0.7
    return min(a, b) / max(a, b, 1)


def category_similarity(
    main_id: int | None,
    other_id: int | None,
    *,
    main_name: str | None = None,
    other_name: str | None = None,
) -> float:
    if main_id is not None and other_id is not None:
        return 1.0 if int(main_id) == int(other_id) else 0.15
    if main_name and other_name:
        return 1.0 if main_name.strip().lower() == other_name.strip().lower() else 0.2
    return 0.4


def score_candidate(
    *,
    main_title: str | None,
    main_price: int | None,
    main_rating: float | None,
    main_feedbacks: int | None,
    main_category_id: int | None,
    main_category_name: str | None,
    candidate: CompetitorCandidate,
) -> tuple[float, dict[str, float]]:
    parts = {
        "title": title_similarity(main_title, candidate.title),
        "category": category_similarity(
            main_category_id,
            candidate.category_id,
            main_name=main_category_name,
            other_name=candidate.category_name,
        ),
        "price": price_similarity(main_price, candidate.price),
        "rating": rating_similarity(main_rating, candidate.rating),
        "feedbacks": feedbacks_similarity(main_feedbacks, candidate.feedbacks),
    }
    total = (
        0.45 * parts["title"]
        + 0.20 * parts["category"]
        + 0.20 * parts["price"]
        + 0.08 * parts["rating"]
        + 0.07 * parts["feedbacks"]
    )
    return total, parts


def _snapshot_from_product_context(product_context: Any) -> dict[str, Any]:
    product = getattr(product_context, "product", None)
    pricing = getattr(product_context, "pricing", None)
    return {
        "article": getattr(product, "article", None) if product else None,
        "title": getattr(product, "title", None) if product else None,
        "brand": getattr(product, "brand", None) if product else None,
        "price": getattr(pricing, "price", None) if pricing else None,
        "rating": getattr(pricing, "rating", None) if pricing else None,
        "feedbacks": getattr(pricing, "feedback_count", None) if pricing else None,
        "category_id": getattr(product_context, "category_id", None),
        "category_name": getattr(product_context, "category_name", None),
    }


class CompetitorMatcher:
    """find_similar_products(product_context) → top N конкурентов."""

    def __init__(self, *, top_n: int = 5, min_score: float = 0.22) -> None:
        self.top_n = max(1, int(top_n))
        self.min_score = float(min_score)

    def find_similar_products(
        self,
        product_context: Any,
        candidates: list[CompetitorCandidate] | None = None,
        *,
        top_n: int | None = None,
    ) -> list[CompetitorCandidate]:
        snap = _snapshot_from_product_context(product_context)
        main_article = snap.get("article")
        scored: list[CompetitorCandidate] = []

        for cand in candidates or []:
            if main_article is not None and cand.article == int(main_article):
                continue
            total, parts = score_candidate(
                main_title=snap.get("title"),
                main_price=snap.get("price"),
                main_rating=snap.get("rating"),
                main_feedbacks=snap.get("feedbacks"),
                main_category_id=snap.get("category_id"),
                main_category_name=snap.get("category_name"),
                candidate=cand,
            )
            cand.score = total
            cand.score_parts = parts
            if total >= self.min_score:
                scored.append(cand)

        scored.sort(key=lambda c: c.score, reverse=True)
        limit = self.top_n if top_n is None else max(1, int(top_n))
        return scored[:limit]
