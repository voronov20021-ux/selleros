"""
Discovery конкурентов через существующий SearchService.

Browser = 0. Нет нового поискового клиента. CostGuard + cache.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.competitor_intelligence.comparison import (
    compare_with_competitors,
    evidence_from_candidate,
)
from backend.competitor_intelligence.enrichment import CompetitorEnricher
from backend.competitor_intelligence.models import (
    DEFAULT_ENRICH_N,
    CompetitorEvidence,
    DiscoveryResult,
    ProductCompetitorProfile,
    UNKNOWN,
)
from backend.competitor_intelligence.parser import parse_search_items
from backend.competitor_intelligence.profile import build_search_query
from backend.competitor_intelligence.ranking import rank_candidates
from backend.competitor_intelligence.store import CompetitorEvidenceStore

log = logging.getLogger("selleros.competitor_intelligence.search")


def _evidence_from_cached(row: dict[str, Any]) -> CompetitorEvidence | None:
    cid = row.get("competitor_id")
    url = row.get("source_url") or ""
    if not cid:
        return None

    def _n(v):
        return None if v in (None, UNKNOWN, "UNKNOWN") else v

    price = _n(row.get("price"))
    rating = _n(row.get("rating"))
    feedbacks = _n(row.get("feedbacks"))
    try:
        price = int(price) if price is not None else None
    except (TypeError, ValueError):
        price = None
    try:
        rating = float(rating) if rating is not None else None
    except (TypeError, ValueError):
        rating = None
    try:
        feedbacks = int(feedbacks) if feedbacks is not None else None
    except (TypeError, ValueError):
        feedbacks = None
    nm_id = _n(row.get("nm_id"))
    try:
        nm_id = int(nm_id) if nm_id is not None else None
    except (TypeError, ValueError):
        nm_id = None
    old_price = _n(row.get("old_price"))
    try:
        old_price = int(old_price) if old_price is not None else None
    except (TypeError, ValueError):
        old_price = None
    photo_count = _n(row.get("photo_count"))
    try:
        photo_count = int(photo_count) if photo_count is not None else None
    except (TypeError, ValueError):
        photo_count = None
    char_count = _n(row.get("char_count"))
    try:
        char_count = int(char_count) if char_count is not None else None
    except (TypeError, ValueError):
        char_count = None
    fields = row.get("fields") if isinstance(row.get("fields"), dict) else {}
    ev = CompetitorEvidence(
        competitor_id=str(cid),
        source=str(row.get("source") or "yandex_search"),
        source_url=str(url),
        title=_n(row.get("title")),
        brand=_n(row.get("brand")),
        category=_n(row.get("category")),
        price=price,
        rating=rating,
        feedbacks=feedbacks,
        discount=_n(row.get("discount")),
        photo_count=photo_count,
        characteristics=_n(row.get("characteristics")) if isinstance(row.get("characteristics"), dict) else None,
        description=_n(row.get("description")),
        available_positioning=_n(row.get("available_positioning")),
        matched_attributes=list(row.get("matched_attributes") or []),
        confidence=float(row.get("confidence") or 0.0),
        retrieved_at=float(row.get("retrieved_at") or 0.0),
        source_timestamp=_n(row.get("source_timestamp")),
        marketplace=_n(row.get("marketplace")),
        nm_id=nm_id,
        char_count=char_count,
        old_price=old_price,
        quality=str(row.get("quality") or ""),
        fields=fields,
        enriched_at=_n(row.get("enriched_at")),
        enrichment_source=_n(row.get("enrichment_source")),
    )
    try:
        if ev.enriched_at is not None:
            ev.enriched_at = float(ev.enriched_at)
    except (TypeError, ValueError):
        ev.enriched_at = None
    ev.refresh_quality()
    return ev


class SearchCompetitorCollector:
    """
    Product profile → SearchService.search_and_store → rank → top-N enrich → evidence.

    Не запускает Browser. Не ходит в search.wb.ru. Enrichment — cache / card.wb.ru.
    """

    def __init__(
        self,
        search_service: Any,
        *,
        intel_store: Any = None,
        top_n: int = DEFAULT_ENRICH_N,
        min_score: float = 0.28,
        public_cache: Any = None,
        detail_fetcher: Any = None,
        enrich_ttl: float | None = None,
    ) -> None:
        self._search = search_service
        self._intel = intel_store or getattr(search_service, "_store", None)
        self._cache = CompetitorEvidenceStore(self._intel) if self._intel is not None else None
        self.top_n = max(1, min(int(top_n), 10))
        self.min_score = float(min_score)
        self.last_http_calls = 0
        self.last_cache_hits = 0
        self.last_guard_status: str | None = None
        self.browser_calls = 0
        self.last_enrich_http = 0
        self.last_enrich_cache = 0
        self._enricher = CompetitorEnricher(
            public_cache=public_cache,
            evidence_store=self._cache,
            detail_fetcher=detail_fetcher,
            top_n=self.top_n,
            ttl_seconds=enrich_ttl,
        )

    async def _enrich_and_compare(
        self,
        profile: ProductCompetitorProfile,
        selected: list[CompetitorEvidence],
        *,
        query: str,
        product_id: str,
        limit: int,
        stats: dict,
        raw_items_n: int,
        candidates: list,
        from_cache: bool,
    ) -> DiscoveryResult:
        enriched, estats = await self._enricher.enrich(
            selected[:limit],
            query=query,
            product_id=product_id,
            top_n=limit,
        )
        self.last_enrich_http = estats.http_calls
        self.last_enrich_cache = estats.cache_hits
        self.browser_calls = 0
        stats = dict(stats)
        stats["enrich_http_calls"] = estats.http_calls
        stats["enrich_cache_hits"] = estats.cache_hits
        stats["enriched"] = estats.enriched
        stats["browser_calls"] = 0
        if self._cache is not None and enriched:
            try:
                await self._cache.save_rows(
                    query=query,
                    product_id=product_id,
                    rows=[e.as_dict() for e in enriched],
                )
            except Exception as exc:
                log.debug("competitor cache save skip: %s", exc)
        comparison = compare_with_competitors(
            profile,
            enriched,
            search_stats=stats,
            query=query,
            discovered_n=int(stats.get("discovered") or raw_items_n or len(enriched)),
        )
        return DiscoveryResult(
            query=query,
            profile=profile,
            raw_items_n=raw_items_n,
            candidates=candidates,
            selected=enriched,
            comparison=comparison,
            search_http_calls=int(stats.get("search_http_calls") or 0),
            search_cache_hits=int(stats.get("search_cache_hits") or 0),
            cost_guard_status=stats.get("cost_guard"),
            browser_calls=0,
            from_competitor_cache=from_cache,
            enrich_http_calls=estats.http_calls,
            enrich_cache_hits=estats.cache_hits,
            enriched_n=estats.enriched,
        )

    async def discover(
        self,
        profile: ProductCompetitorProfile,
        *,
        top_n: int | None = None,
    ) -> DiscoveryResult:
        self.browser_calls = 0
        query = build_search_query(profile)
        product_id = str(profile.article or "unknown")
        limit = self.top_n if top_n is None else max(1, min(int(top_n), 10))

        cached_rows: list[dict] = []
        if self._cache is not None:
            try:
                cached_rows = await self._cache.load_fresh(query=query, product_id=product_id)
            except Exception as exc:
                log.debug("competitor cache load skip: %s", exc)
                cached_rows = []

        if cached_rows:
            selected = []
            for row in cached_rows:
                ev = _evidence_from_cached(row)
                if ev is not None:
                    selected.append(ev)
            selected = selected[:limit]
            self.last_http_calls = 0
            self.last_cache_hits = 1
            self.last_guard_status = "competitor_cache"
            return await self._enrich_and_compare(
                profile,
                selected,
                query=query,
                product_id=product_id,
                limit=limit,
                stats={
                    "search_http_calls": 0,
                    "search_cache_hits": 1,
                    "cost_guard": "competitor_cache",
                    "browser_calls": 0,
                    "discovered": len(cached_rows),
                    "selected": len(selected),
                },
                raw_items_n=len(cached_rows),
                candidates=[],
                from_cache=True,
            )

        items: list = []
        http_calls = 0
        cache_hits = 0
        guard_status = None
        if self._search is None:
            log.warning("SearchCompetitorCollector: search_service is None")
        else:
            guard = getattr(self._search, "cost_guard", None) or getattr(self._search, "_guard", None)
            pre_from_cache = False
            try:
                if hasattr(self._search, "ensure_source_registered"):
                    await self._search.ensure_source_registered()
            except Exception:
                pass
            if guard is not None and hasattr(guard, "check"):
                try:
                    result = await guard.check(query, profile.category, "RU")
                    guard_status = getattr(getattr(result, "status", None), "value", None) or str(
                        getattr(result, "status", "") or ""
                    )
                    pre_from_cache = bool(getattr(result, "from_cache", False))
                except Exception:
                    pass
            adapter = getattr(self._search, "_adapter", None)
            before = getattr(adapter, "http_calls", None)
            try:
                items = await self._search.search_and_store(
                    query=query,
                    category=profile.category,
                    region="RU",
                )
            except Exception as exc:
                log.warning("SearchCompetitorCollector: search failed: %s", exc)
                items = []
            after = getattr(adapter, "http_calls", None)
            if isinstance(before, int) and isinstance(after, int):
                http_calls = max(0, after - before)
            elif pre_from_cache:
                http_calls = 0
                cache_hits = 1
            elif guard_status in ("cache_hit", "cached_limit"):
                http_calls = 0
                cache_hits = 1
            else:
                http_calls = 1 if items else 0
            if http_calls == 0 and items:
                cache_hits = max(cache_hits, 1)

        self.last_http_calls = http_calls
        self.last_cache_hits = cache_hits
        self.last_guard_status = guard_status

        parsed = parse_search_items(items)
        ranked = rank_candidates(
            profile,
            parsed,
            top_n=max(limit, 10),
            min_score=self.min_score,
            exclude_article=profile.article,
        )
        selected = [evidence_from_candidate(c) for c in ranked[:limit]]
        stats = {
            "search_http_calls": http_calls,
            "search_cache_hits": cache_hits,
            "cost_guard": guard_status,
            "browser_calls": 0,
            "discovered": len(parsed),
            "selected": len(selected),
        }
        return await self._enrich_and_compare(
            profile,
            selected,
            query=query,
            product_id=product_id,
            limit=limit,
            stats=stats,
            raw_items_n=len(items or []),
            candidates=parsed,
            from_cache=False,
        )
