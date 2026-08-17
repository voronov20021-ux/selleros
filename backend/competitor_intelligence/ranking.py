"""
Deterministic weighted ranking кандидатов SearchService.

30 results ≠ 30 competitors. Top N (configurable, default 8).
"""

from __future__ import annotations

import re
from backend.competitor_intelligence.models import (
    DEFAULT_TOP_N,
    ProductCompetitorProfile,
    SearchCandidate,
)

_STOPWORDS = frozenset({
    "для", "и", "или", "на", "в", "с", "со", "по", "от", "из", "к", "ко",
    "а", "но", "the", "and", "or", "of", "to", "a", "an", "шт", "см", "мм",
    "site", "wildberries", "ru",
})

_TYPE_KEYS: dict[str, tuple[str, ...]] = {
    "кроссовки": ("кроссов", "sneaker", "кед"),
    "кеды": ("кед", "кроссов"),
    "ботинки": ("ботин",),
    "туфли": ("туфл",),
    "куртка": ("куртк", "бомбер", "ветровк"),
    "футболка": ("футболк", "t-shirt"),
    "часы": ("час", "watch"),
    "очки": ("очк", "glasses"),
    "наушники": ("наушник", "headset"),
    "чехол": ("чехол", "case"),
    "пылесос": ("пылесос", "vacuum"),
}

WEIGHTS = {
    "category_match": 0.22,
    "product_type_match": 0.18,
    "attribute_match": 0.18,
    "keyword_match": 0.16,
    "price_relevance": 0.12,
    "brand_model": 0.08,
    "marketplace": 0.06,
}

DEFAULT_MIN_SCORE = 0.28


def _tokens(text: str | None) -> set[str]:
    if not text:
        return set()
    parts = re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", str(text).lower().replace("ё", "е"))
    return {p for p in parts if len(p) >= 2 and p not in _STOPWORDS}


def _blob(cand: SearchCandidate) -> str:
    return " ".join(
        x for x in (
            cand.title, cand.snippet, cand.category, cand.brand, cand.description,
        ) if x
    ).lower().replace("ё", "е")


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def _category_match(profile: ProductCompetitorProfile, cand: SearchCandidate) -> float:
    cat = (profile.category or "").lower().replace("ё", "е")
    if not cat:
        return 0.45
    blob = _blob(cand)
    cat_toks = _tokens(cat)
    if cat in blob or (cat_toks and cat_toks <= _tokens(blob)):
        return 1.0
    if cat_toks and _jaccard(cat_toks, _tokens(blob)) >= 0.4:
        return 0.7
    cand_cat = (cand.category or "").lower().replace("ё", "е")
    if cand_cat and (cand_cat == cat or cat in cand_cat or cand_cat in cat):
        return 0.85
    return 0.12


def _product_type_match(profile: ProductCompetitorProfile, cand: SearchCandidate) -> float:
    ptype = (profile.product_type or "").lower().replace("ё", "е")
    if not ptype:
        return 0.45
    blob = _blob(cand)
    keys = _TYPE_KEYS.get(ptype, (ptype[:5],))
    if any(k in blob for k in keys if len(k) >= 3):
        return 1.0
    if ptype in blob:
        return 0.9
    return 0.08


def _attribute_match(profile: ProductCompetitorProfile, cand: SearchCandidate) -> tuple[float, list[str]]:
    blob = _blob(cand)
    matched: list[str] = []
    attrs: list[str] = []
    for val in (profile.key_characteristics or {}).values():
        for tok in _tokens(str(val)):
            if len(tok) >= 3:
                attrs.append(tok)
    for kw in profile.keywords:
        if kw not in attrs and len(kw) >= 3 and not kw.isdigit():
            attrs.append(kw)
    # color/gender-ish tokens from title keywords only if present on card
    if not attrs:
        return 0.45, matched
    hits = 0
    for a in attrs[:8]:
        if a in blob:
            hits += 1
            matched.append(a)
    ratio = hits / max(1, min(len(attrs), 8))
    return ratio, matched


def _keyword_match(profile: ProductCompetitorProfile, cand: SearchCandidate) -> float:
    kws = set(profile.keywords or [])
    if not kws:
        return _jaccard(_tokens(profile.title), _tokens(cand.title))
    return _jaccard(kws, _tokens(_blob(cand)))


def _price_relevance(profile: ProductCompetitorProfile, cand: SearchCandidate) -> float:
    if profile.price is None or cand.price is None or profile.price <= 0 or cand.price <= 0:
        return 0.40
    ratio = min(profile.price, cand.price) / max(profile.price, cand.price)
    if ratio >= 0.55:
        return ratio
    return max(0.0, ratio * 0.4)


def _brand_model(profile: ProductCompetitorProfile, cand: SearchCandidate) -> float:
    brand = (profile.brand or "").lower().strip()
    blob = _blob(cand)
    if not brand:
        return 0.40
    if brand in blob:
        return 1.0
    title_toks = _tokens(cand.title)
    if brand in title_toks:
        return 1.0
    return 0.25


def _marketplace_score(cand: SearchCandidate) -> float:
    mp = (cand.marketplace or "").lower()
    if mp == "wildberries":
        return 1.0
    if mp in ("ozon", "yandex_market"):
        return 0.35
    if cand.source_url and "wildberries" in cand.source_url.lower():
        return 1.0
    return 0.12


def _hard_reject(profile: ProductCompetitorProfile, cand: SearchCandidate) -> str | None:
    mp = (cand.marketplace or "").lower()
    if mp not in ("wildberries", "ozon", "yandex_market") and "wildberries" not in (cand.source_url or "").lower():
        return "marketplace"
    ptype = (profile.product_type or "").lower().replace("ё", "е")
    blob = _blob(cand)
    if not ptype or not blob.strip():
        return None
    own_keys = _TYPE_KEYS.get(ptype, (ptype[:5],))
    own_hit = any(k in blob for k in own_keys if len(k) >= 3) or ptype in blob
    if own_hit:
        return None
    for other, keys in _TYPE_KEYS.items():
        if other == ptype:
            continue
        if any(k in blob for k in keys if len(k) >= 4):
            cat_ok = _category_match(profile, cand) >= 0.7
            if not cat_ok:
                return "category_mismatch"
    if _category_match(profile, cand) < 0.18 and _product_type_match(profile, cand) < 0.15:
        return "category_mismatch"
    return None


def score_candidate(
    profile: ProductCompetitorProfile,
    cand: SearchCandidate,
) -> tuple[float, dict[str, float], list[str], str | None]:
    reject = _hard_reject(profile, cand)
    attr_s, matched = _attribute_match(profile, cand)
    parts = {
        "category_match": _category_match(profile, cand),
        "product_type_match": _product_type_match(profile, cand),
        "attribute_match": attr_s,
        "keyword_match": _keyword_match(profile, cand),
        "price_relevance": _price_relevance(profile, cand),
        "brand_model": _brand_model(profile, cand),
        "marketplace": _marketplace_score(cand),
    }
    total = sum(WEIGHTS[k] * parts[k] for k in WEIGHTS)
    if reject:
        return 0.0, parts, matched, reject
    return total, parts, matched, None


def rank_candidates(
    profile: ProductCompetitorProfile,
    candidates: list[SearchCandidate],
    *,
    top_n: int = DEFAULT_TOP_N,
    min_score: float = DEFAULT_MIN_SCORE,
    exclude_article: int | None = None,
) -> list[SearchCandidate]:
    scored: list[SearchCandidate] = []
    seen: set[str] = set()
    exclude = int(exclude_article) if exclude_article is not None else None

    for cand in candidates or []:
        if exclude is not None and cand.nm_id is not None and int(cand.nm_id) == exclude:
            continue
        if cand.competitor_id in seen:
            continue
        seen.add(cand.competitor_id)
        total, parts, matched, reject = score_candidate(profile, cand)
        cand.score = total
        cand.score_parts = parts
        cand.matched_attributes = matched
        cand.reject_reason = reject
        if reject:
            continue
        if total < min_score:
            if parts.get("attribute_match", 1.0) < 0.15 and parts.get("keyword_match", 1.0) < 0.15:
                cand.reject_reason = "attribute_mismatch"
            continue
        scored.append(cand)

    scored.sort(key=lambda c: c.score, reverse=True)
    limit = max(1, min(int(top_n), 10))
    return scored[:limit]
