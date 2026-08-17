"""
Enrichment top-N конкурентов после ranking.

Источники (в порядке):
  1) competitor evidence cache (тот же competitor + query, в пределах TTL);
  2) PublicProductCache HIT (без Browser refresh);
  3) публичный card.wb.ru/cards/v4/detail (существующий ProductCardProvider);
  4) нет поля → UNKNOWN.

Не использует BrowserFetcher, search.wb.ru, feedbacks*.wb.ru, RI, SFP, WB Engine internals.
Не увеличивает Search API usage.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable, Sequence

from backend.competitor_intelligence.models import (
    DEFAULT_ENRICH_N,
    CompetitorEvidence,
    EvidenceQuality,
    UNKNOWN,
    evidence_quality_of,
    field_record,
)
from backend.competitor_intelligence.parser import competitor_identity

log = logging.getLogger("selleros.competitor_intelligence.enrich")

DetailFetcher = Callable[[list[int]], Awaitable[dict[int, dict[str, Any]]]]

SRC_CACHE = "competitor_cache"
SRC_PUBLIC_CACHE = "public_cache"
SRC_CARD = "card.wb.ru"
SRC_SEARCH = "yandex_search"

_ENRICH_FIELDS = (
    "nm_id", "title", "brand", "price", "old_price", "discount",
    "rating", "feedbacks", "photo_count", "char_count", "category",
)


def _safe_int(value: Any) -> int | None:
    if value is None or value is False or value == UNKNOWN:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    if value is None or value is False or value == UNKNOWN:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_str(value: Any) -> str | None:
    if value is None or value == UNKNOWN:
        return None
    text = str(value).strip()
    return text or None


def _within_ttl(ts: float | None, ttl: float, *, now: float | None = None) -> bool:
    if not ts:
        return False
    return (now if now is not None else time.time()) - float(ts) < float(ttl)


def rebuild_fields(ev: CompetitorEvidence) -> dict[str, Any]:
    ts = ev.source_timestamp or ev.enriched_at or ev.retrieved_at
    src = ev.enrichment_source or ev.source
    out: dict[str, Any] = {}
    for name in _ENRICH_FIELDS:
        out[name] = field_record(getattr(ev, name, None), source=src, source_timestamp=ts)
    return out


def apply_quality(ev: CompetitorEvidence) -> CompetitorEvidence:
    ev.refresh_quality()
    ev.fields = rebuild_fields(ev)
    return ev


def _overlay_known(ev: CompetitorEvidence, name: str, value: Any) -> None:
    """Коммерческие поля из public/CDN перекрывают snippet, но не затирают None."""
    if value is None:
        return
    setattr(ev, name, value)


@dataclass
class EnrichmentStats:
    attempted: int = 0
    enriched: int = 0
    cache_hits: int = 0
    public_cache_hits: int = 0
    http_calls: int = 0
    skipped_ttl: int = 0
    skipped_no_nm: int = 0
    rate_gated: int = 0
    browser_calls: int = 0


def _product_article(product: Any) -> int | None:
    try:
        return int(getattr(product, "article", None))
    except (TypeError, ValueError):
        return None


def _map_public_product(ev: CompetitorEvidence, product: Any, *, source: str) -> CompetitorEvidence:
    """Перенести только nm-поля. Не берёт IMT / history / чужой article."""
    art = _product_article(product)
    expected = ev.nm_id
    if art is None:
        return ev
    if expected is not None and int(art) != int(expected):
        log.info(
            "enrich skip nm mismatch expected=%s got=%s",
            expected, art,
        )
        return ev
    src_label = str(getattr(product, "source", "") or "")
    if src_label == "history":
        return ev

    now = time.time()
    out = replace(ev)
    if out.nm_id is None:
        out.nm_id = art
    new_id = competitor_identity(url=out.source_url, nm_id=out.nm_id)
    if new_id != out.competitor_id:
        out.competitor_id = new_id

    _overlay_known(out, "title", _safe_str(getattr(product, "title", None)))
    _overlay_known(out, "brand", _safe_str(getattr(product, "brand", None)))
    cat = _safe_str(getattr(product, "subject_name", None)) or _safe_str(
        getattr(product, "subject_root_name", None)
    )
    _overlay_known(out, "category", cat)
    _overlay_known(out, "price", _safe_int(getattr(product, "price", None)))
    _overlay_known(out, "old_price", _safe_int(getattr(product, "old_price", None)))
    discount = _safe_int(getattr(product, "discount", None))
    if discount is not None and (out.old_price or discount > 0):
        out.discount = discount
    _overlay_known(out, "rating", _safe_float(getattr(product, "rating", None)))
    fb = getattr(product, "feedbacks", None)
    if fb is not None:
        try:
            out.feedbacks = int(fb)
        except (TypeError, ValueError):
            pass
    pics = _safe_int(getattr(product, "photo_count", None))
    proven = None
    prov = getattr(product, "field_provenance", None) or {}
    if isinstance(prov, dict) and prov.get("photo_count"):
        proven = _safe_int(getattr(product, "photo_count", None))
    if proven is not None:
        out.photo_count = proven
    elif pics is not None and pics > 0:
        out.photo_count = pics
    chars = getattr(product, "characteristics", None)
    if isinstance(chars, dict) and chars:
        out.characteristics = dict(chars)
        out.char_count = len(chars)
    elif isinstance(chars, (list, tuple)) and chars:
        out.char_count = len(chars)

    out.enrichment_source = source
    out.enriched_at = now
    out.source_timestamp = now
    if source != SRC_SEARCH:
        out.source = source
    return apply_quality(out)


def _map_detail_raw(ev: CompetitorEvidence, raw: dict[str, Any]) -> CompetitorEvidence:
    """card.wb.ru raw → evidence через существующий apply_detail (nm-only)."""
    from backend.wb.cdn_provider import WBProduct, apply_detail
    from backend.wb.provenance import raw_nm_id

    raw_id = raw_nm_id(raw)
    try:
        raw_id_i = int(raw_id) if raw_id is not None else None
    except (TypeError, ValueError):
        raw_id_i = None
    expected = ev.nm_id
    if raw_id_i is None:
        return ev
    if expected is not None and raw_id_i != int(expected):
        log.info("enrich identity split expected=%s card=%s", expected, raw_id_i)
        return ev
    product = WBProduct(article=raw_id_i)
    apply_detail(product, raw)
    return _map_public_product(ev, product, source=SRC_CARD)


def _needs_fetch(ev: CompetitorEvidence, *, ttl: float, now: float) -> bool:
    if ev.nm_id is None:
        return False
    if ev.quality == EvidenceQuality.FULL:
        return False
    if _within_ttl(ev.enriched_at, ttl, now=now):
        return False
    return True


def dedupe_evidence(items: Sequence[CompetitorEvidence]) -> list[CompetitorEvidence]:
    seen: set[str] = set()
    out: list[CompetitorEvidence] = []
    for ev in items:
        cid = ev.competitor_id
        if cid in seen:
            continue
        seen.add(cid)
        out.append(ev)
    return out


async def fetch_card_details(articles: list[int]) -> dict[int, dict[str, Any]]:
    """Один batch card.wb.ru. Без Browser, без search.wb.ru, без card.json basket."""
    if not articles:
        return {}
    try:
        from backend.wb_engine.rate_gate import wb_rate_gate
        if not await wb_rate_gate.try_acquire():
            log.info("enrichment: WB rate gate blocked card.wb.ru")
            return {}
    except Exception as exc:
        log.debug("enrichment rate gate skip: %s", exc)

    try:
        from backend.wb.cdn_provider import AsyncWBClient
    except Exception as exc:
        log.warning("enrichment: AsyncWBClient unavailable: %s", exc)
        return {}

    proxies = None
    try:
        from backend.browser.proxy import normalize_wb_proxy_url
        from backend.config import WB_PROXY_SCHEME, effective_wb_proxy_urls
        urls = [p.strip() for p in (effective_wb_proxy_urls() or "").split(",") if p.strip()]
        if urls:
            proxy_url = normalize_wb_proxy_url(urls[0], scheme=WB_PROXY_SCHEME or "socks5")
            proxies = {"http": proxy_url, "https": proxy_url}
    except Exception as exc:
        log.debug("enrichment proxy skip: %s", exc)

    unique = list(dict.fromkeys(int(a) for a in articles))
    try:
        async with AsyncWBClient(concurrency=1, retries=1, proxies=proxies or None) as client:
            return await client.fetch_detail(unique)
    except Exception as exc:
        log.warning("enrichment card.wb.ru failed: %s", exc)
        return {}


async def _default_fetch_details(articles: list[int]) -> dict[int, dict[str, Any]]:
    return await fetch_card_details(articles)


class CompetitorEnricher:
    """
    Обогащает только top-N. Rate gate: max N на анализ, TTL, CostGuard не трогает Search.
    """

    def __init__(
        self,
        *,
        public_cache: Any = None,
        evidence_store: Any = None,
        detail_fetcher: DetailFetcher | None = None,
        top_n: int = DEFAULT_ENRICH_N,
        ttl_seconds: float | None = None,
        rate_gate: Any = None,
    ) -> None:
        self._public_cache = public_cache
        self._store = evidence_store
        self._fetch = detail_fetcher or _default_fetch_details
        self.top_n = max(1, min(int(top_n), 10))
        if ttl_seconds is None:
            try:
                from backend.config import COMPETITOR_ENRICH_TTL_SECONDS
                ttl_seconds = float(COMPETITOR_ENRICH_TTL_SECONDS)
            except Exception:
                ttl_seconds = 6 * 3600
        self.ttl_seconds = float(ttl_seconds)
        self._rate_gate = rate_gate
        self.last_stats = EnrichmentStats()

    def _public_hit(self, nm_id: int) -> Any | None:
        cache = self._public_cache
        if cache is None or not hasattr(cache, "get_fresh"):
            return None
        try:
            product = cache.get_fresh(int(nm_id))
        except Exception as exc:
            log.debug("public cache skip nm=%s: %s", nm_id, exc)
            return None
        if product is None:
            return None
        art = _product_article(product)
        if art is None or art != int(nm_id):
            return None
        if str(getattr(product, "source", "") or "") == "history":
            return None
        return product

    async def enrich(
        self,
        selected: Sequence[CompetitorEvidence],
        *,
        query: str = "",
        product_id: str = "",
        top_n: int | None = None,
    ) -> tuple[list[CompetitorEvidence], EnrichmentStats]:
        stats = EnrichmentStats()
        limit = self.top_n if top_n is None else max(1, min(int(top_n), 10))
        now = time.time()
        work = [apply_quality(replace(ev)) for ev in list(selected or [])[:limit]]
        work = dedupe_evidence(work)
        stats.attempted = len(work)

        to_fetch: list[int] = []
        fetch_index: dict[int, list[int]] = {}

        for i, ev in enumerate(work):
            if ev.nm_id is None:
                stats.skipped_no_nm += 1
                continue
            if not _needs_fetch(ev, ttl=self.ttl_seconds, now=now):
                stats.skipped_ttl += 1
                stats.cache_hits += 1
                continue
            hit = self._public_hit(int(ev.nm_id))
            if hit is not None:
                work[i] = _map_public_product(ev, hit, source=SRC_PUBLIC_CACHE)
                stats.public_cache_hits += 1
                stats.cache_hits += 1
                if work[i].quality == EvidenceQuality.FULL:
                    continue
            if work[i].nm_id is None:
                continue
            nm = int(work[i].nm_id)
            if len(to_fetch) >= limit:
                break
            if nm not in fetch_index:
                to_fetch.append(nm)
            fetch_index.setdefault(nm, []).append(i)

        if to_fetch:
            if self._rate_gate is not None and hasattr(self._rate_gate, "try_acquire"):
                try:
                    allowed = await self._rate_gate.try_acquire()
                except Exception:
                    allowed = True
                if not allowed:
                    stats.rate_gated = 1
                    to_fetch = []
            if to_fetch:
                details: dict[int, dict[str, Any]] = {}
                try:
                    details = await self._fetch(to_fetch) or {}
                    stats.http_calls = 1 if details or to_fetch else 0
                    if not details:
                        stats.http_calls = 1
                except Exception as exc:
                    log.warning("enrich fetch failed: %s", exc)
                    details = {}
                    stats.http_calls = 1
                for nm, idxs in fetch_index.items():
                    raw = details.get(int(nm))
                    if not raw:
                        for i in idxs:
                            work[i].enriched_at = now
                            apply_quality(work[i])
                        continue
                    for i in idxs:
                        work[i] = _map_detail_raw(work[i], raw)
                        if work[i].quality in (EvidenceQuality.FULL, EvidenceQuality.PARTIAL):
                            stats.enriched += 1

        for ev in work:
            apply_quality(ev)
            if ev.quality in (EvidenceQuality.FULL, EvidenceQuality.PARTIAL) and ev.enriched_at:
                if ev.enrichment_source in (SRC_CARD, SRC_PUBLIC_CACHE):
                    pass

        stats.enriched = sum(
            1 for ev in work
            if ev.enrichment_source in (SRC_CARD, SRC_PUBLIC_CACHE)
            and ev.quality != EvidenceQuality.UNKNOWN
        )
        stats.browser_calls = 0
        self.last_stats = stats

        if self._store is not None:
            try:
                snaps = []
                for ev in work:
                    if ev.price is None and ev.rating is None and ev.feedbacks is None:
                        continue
                    snaps.append({
                        "competitor_id": ev.competitor_id,
                        "query": query,
                        "product_id": product_id,
                        "price": ev.price,
                        "rating": ev.rating,
                        "feedbacks": ev.feedbacks,
                        "captured_at": ev.enriched_at or ev.source_timestamp or now,
                        "source": ev.enrichment_source or ev.source,
                    })
                if snaps and hasattr(self._store, "save_snapshots"):
                    await self._store.save_snapshots(snaps)
            except Exception as exc:
                log.debug("snapshot save skip: %s", exc)

        return work, stats
