"""
Source Fetch Policy — Browser is an expensive fallback, not a per-action fetch.

Rules (TTL window per nm_id):
  1. Verified card in cache/session + TTL valid → NO Browser; reuse snapshot.
  2. Missing field → cheaper sources first; Browser only if field truly needed.
  3. ≤1 Browser detail fetch per fresh TTL window per nm_id (shared cache /
     single-flight); services must not each trigger their own Browser call.
  4–5. Verified Browser snapshot reused everywhere (ProductService → Reviews →
     Analysis → Argus → Advisor); no separate Browser for those stages.
  6. Explicit logs: CACHE HIT / MISS / STALE.
  7. Re-opening the same product is not a reason to refresh.
  8–10. Fresh verified data is enough for Argus; refresh only when stale,
     unproven commercial, or a truly needed field is missing.

ProductService.get_product_snapshot is the sole entry that may call Browser
within TTL. Other layers consume session / public cache / that snapshot.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from backend.services.product_service import has_verified_public_commercial

log = logging.getLogger("selleros.fetch_policy")

# Exact policy phrases (tests / live regression grep these).
MSG_HIT = "CACHE HIT → Browser not called"
MSG_MISS = "CACHE MISS → Browser called"
MSG_STALE = "STALE → refresh allowed"
MSG_REUSE_SESSION = "CACHE HIT → Browser not called (session snapshot)"
MSG_SKIP_DUP = "CACHE HIT → Browser not called (ProductService owns Browser)"


def _snapshot_reusable(product) -> bool:
    """Full verified commercial OR honest partial (no unproven pollution)."""
    if product is None:
        return False
    if has_verified_public_commercial(product):
        return True
    try:
        from backend.providers.browser_provider import is_fresh_snapshot_reusable

        return is_fresh_snapshot_reusable(product)
    except Exception:
        return False


@dataclass
class FetchDecision:
    """Outcome of a policy check for one nm_id."""

    article: int
    cache_status: str  # HIT | STALE | MISS | SESSION | UNKNOWN
    browser_allowed: bool
    reuse_product: Any | None = None
    reason: str = ""


def inspect_public_cache(cache: Any, article: int) -> str:
    """Return HIT / STALE / MISS / UNKNOWN without side effects."""
    if cache is None:
        return "UNKNOWN"
    try:
        status = cache.inspect(int(article))
        return getattr(status, "value", None) or str(status)
    except Exception:
        return "UNKNOWN"


def log_policy(article: int, message: str, *, detail: str = "") -> None:
    """Emit a single explicit policy line."""
    article = int(article)
    if detail:
        log.info("%s article=%s %s", message, article, detail)
    else:
        log.info("%s article=%s", message, article)


def decide_browser_for_cache_status(
    article: int,
    cache_status: str,
    *,
    product: Any | None = None,
) -> FetchDecision:
    """
    Map cache status (+ optional product) → whether Browser may run.

    HIT + verified commercial → no Browser.
    HIT without verified commercial → treat as need refresh (caller expires).
    STALE / MISS → Browser allowed.
    """
    article = int(article)
    status = (cache_status or "UNKNOWN").upper()

    if status == "HIT":
        if product is not None and _snapshot_reusable(product):
            return FetchDecision(
                article=article,
                cache_status="HIT",
                browser_allowed=False,
                reuse_product=product,
                reason="verified_snapshot",
            )
        return FetchDecision(
            article=article,
            cache_status="HIT",
            browser_allowed=True,
            reuse_product=None,
            reason="hit_unverified_commercial",
        )

    if status == "STALE":
        return FetchDecision(
            article=article,
            cache_status="STALE",
            browser_allowed=True,
            reason="ttl_expired",
        )

    if status == "MISS":
        return FetchDecision(
            article=article,
            cache_status="MISS",
            browser_allowed=True,
            reason="no_cache_entry",
        )

    return FetchDecision(
        article=article,
        cache_status=status,
        browser_allowed=True,
        reason="unknown_cache",
    )


def try_reuse_verified_snapshot(
    article: int,
    *,
    session_product: Any | None = None,
    public_cache: Any = None,
    force_refresh: bool = False,
) -> FetchDecision:
    """
    Reuse session / public-cache snapshot when TTL is valid and commercial
    is nm-verified. Used on re-open so Browser is not called again.
    """
    article = int(article)
    if force_refresh:
        status = inspect_public_cache(public_cache, article)
        if status == "STALE":
            log_policy(article, MSG_STALE, detail="force_refresh")
        elif status == "MISS":
            log_policy(article, MSG_MISS, detail="force_refresh")
        else:
            log_policy(article, MSG_STALE, detail="force_refresh override")
        return FetchDecision(
            article=article,
            cache_status=status if status != "UNKNOWN" else "MISS",
            browser_allowed=True,
            reason="force_refresh",
        )

    # 1) Session product same nm + reusable commercial + cache not STALE
    if (
        session_product is not None
        and getattr(session_product, "article", None) is not None
        and int(session_product.article) == article
        and _snapshot_reusable(session_product)
    ):
        status = inspect_public_cache(public_cache, article)
        if status in ("HIT", "UNKNOWN"):
            # UNKNOWN = no cache wired — still reuse session within process
            log_policy(article, MSG_REUSE_SESSION, detail=f"cache={status}")
            return FetchDecision(
                article=article,
                cache_status="SESSION" if status == "UNKNOWN" else "HIT",
                browser_allowed=False,
                reuse_product=session_product,
                reason="session_verified",
            )
        if status == "STALE":
            log_policy(article, MSG_STALE, detail="session present but cache stale")
            return FetchDecision(
                article=article,
                cache_status="STALE",
                browser_allowed=True,
                reason="session_stale_cache",
            )

    # 2) Fresh public cache with reusable commercial
    if public_cache is not None:
        status = inspect_public_cache(public_cache, article)
        if status == "HIT":
            product = None
            try:
                product = public_cache.get_fresh(article)
            except Exception:
                product = None
            if product is not None and _snapshot_reusable(product):
                log_policy(article, MSG_HIT, detail="public_cache")
                return FetchDecision(
                    article=article,
                    cache_status="HIT",
                    browser_allowed=False,
                    reuse_product=product,
                    reason="public_cache_verified",
                )
            # HIT but unproven — allow Browser (caller/provider will refresh)
            return FetchDecision(
                article=article,
                cache_status="HIT",
                browser_allowed=True,
                reason="public_cache_unverified",
            )
        if status == "STALE":
            log_policy(article, MSG_STALE)
            return FetchDecision(
                article=article,
                cache_status="STALE",
                browser_allowed=True,
                reason="public_cache_stale",
            )
        if status == "MISS":
            return FetchDecision(
                article=article,
                cache_status="MISS",
                browser_allowed=True,
                reason="public_cache_miss",
            )

    return FetchDecision(
        article=article,
        cache_status="MISS",
        browser_allowed=True,
        reason="no_snapshot",
    )


def product_service_owns_browser(product_service: Any) -> bool:
    """True if BrowserProvider is registered on ProductService."""
    if product_service is None:
        return False
    providers = getattr(product_service, "_providers", None) or {}
    try:
        for chain in providers.values():
            for item in chain:
                provider = item[1] if isinstance(item, (tuple, list)) else item
                if getattr(provider, "name", None) == "browser":
                    return True
    except Exception:
        return False
    return False
