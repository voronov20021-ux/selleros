"""
BrowserProvider — PRIORITY №1 источник товара для ProductService.

Цепочка (Source Fetch Policy):
  CACHE HIT (verified / honest snapshot, TTL valid) → Browser not called
  HIT with unproven/IMT commercial → expire → browser refetch
  STALE → refresh allowed → browser fetch (single-flight)
  CACHE MISS → Browser called → cache → return
  browser fail → None → ProductService идёт в WBEngine/HTTP fallback

Не смешивает seller-specific данные с публичным кэшем.
Не делает aggressive refresh только из-за reopen того же nm_id.
"""

from __future__ import annotations

import logging

from backend.browser.cache import CacheStatus, PublicProductCache
from backend.browser.fetcher import BrowserFetchError, BrowserFetcherProtocol
from backend.browser.reviews_cache import PublicReviewsCache
from backend.providers.base import ProductProvider
from backend.wb.cdn_provider import WBProduct, _sync_imt_root
from backend.wb.provenance import is_nm_verified, is_unverified_or_imt

log = logging.getLogger("selleros.browser.provider")


def has_nm_verified_commercial(product: WBProduct | None) -> bool:
    """price+rating+feedbacks все nm-verified (browser detail intercept / DOM nm-proof)."""
    if product is None:
        return False
    return all(
        is_nm_verified(product, name)
        and getattr(product, name, None) not in (None, "", [], {})
        for name in ("price", "rating", "feedbacks")
    )


def is_fresh_snapshot_reusable(product: WBProduct | None) -> bool:
    """
    Fresh TTL snapshot may be reused even if some commercial fields are None
    (WB genuinely has no rating yet). Refetch only when present values look
    unproven / IMT-polluted — not on every reopen of an incomplete card.
    """
    if product is None:
        return False
    present_ok = False
    for name in ("price", "rating", "feedbacks"):
        val = getattr(product, name, None)
        if val in (None, "", [], {}):
            continue
        if is_unverified_or_imt(product, name) or not is_nm_verified(product, name):
            return False
        present_ok = True
    if present_ok:
        return True
    # No commercial at all — only reuse if clearly a browser snapshot with identity
    src = (getattr(product, "source", None) or "").strip().lower()
    return src in ("browser", "browser_cache") and bool(getattr(product, "title", None))


class BrowserProvider(ProductProvider):
    name = "browser"
    marketplace = "wildberries"

    def __init__(
        self,
        *,
        cache: PublicProductCache,
        fetcher: BrowserFetcherProtocol,
        reviews_cache: PublicReviewsCache | None = None,
        retries: int = 1,
        enabled: bool = True,
    ):
        self.cache = cache
        self.fetcher = fetcher
        self.reviews_cache = reviews_cache
        self.retries = max(1, int(retries))
        self.enabled = bool(enabled)
        # тест/метрики
        self.browser_fetch_count = 0
        self.last_cache_status: str | None = None
        self.last_browser_status: str | None = None

    async def is_available(self) -> bool:
        if not self.enabled:
            return False
        try:
            return bool(self.fetcher.is_available())
        except Exception:
            return False

    async def get_product(self, article: int) -> WBProduct | None:
        article = int(article)
        status = self.cache.inspect(article)
        self.last_cache_status = status.value

        if status == CacheStatus.HIT:
            product = self.cache.get_fresh(article)
            if product is not None and (
                has_nm_verified_commercial(product)
                or is_fresh_snapshot_reusable(product)
            ):
                # Source Fetch Policy: verified/honest + TTL valid → NO Browser
                log.info(
                    "CACHE HIT → Browser not called article=%s",
                    article,
                )
                self.last_browser_status = "SKIPPED_CACHE_HIT"
                _sync_imt_root(product)
                return product
            if product is not None:
                # HIT есть, но commercial без nm-proof — expire + refetch browser
                # (иначе get_or_fetch вернёт HIT; ProductService cleared → None
                # при card.wb.ru 403)
                log.info(
                    "BrowserProvider: article=%s cache=HIT but commercial "
                    "unproven → browser refetch",
                    article,
                )
                try:
                    self.cache.force_expire(article)
                except Exception as exc:
                    log.debug("force_expire skip: %s", exc)
                status = CacheStatus.STALE
                self.last_cache_status = status.value
            else:
                status = CacheStatus.MISS
                self.last_cache_status = status.value

        if status == CacheStatus.STALE:
            log.info("STALE → refresh allowed article=%s", article)
        else:
            log.info("CACHE MISS → Browser called article=%s", article)

        log.info("BrowserProvider: article=%s browser_fetch_start", article)

        async def _do_fetch() -> WBProduct | None:
            last_err: Exception | None = None
            for attempt in range(self.retries):
                try:
                    self.browser_fetch_count += 1
                    product, reviews = await self.fetcher.fetch(article)
                    if product is None:
                        raise BrowserFetchError("fetcher returned None")
                    product.source = getattr(product, "source", None) or "browser"
                    _sync_imt_root(product)
                    if self.reviews_cache is not None:
                        try:
                            self.reviews_cache.set_reviews(
                                article,
                                reviews or [],
                                imt_id=getattr(product, "imt_id", None),
                            )
                        except Exception as exc:
                            log.debug("reviews cache set skip: %s", exc)
                    return product
                except Exception as exc:
                    last_err = exc
                    log.warning(
                        "BrowserProvider: article=%s browser attempt %s/%s failed: %s",
                        article, attempt + 1, self.retries, _safe_err(exc),
                    )
            if last_err is not None:
                raise last_err
            return None

        try:
            final_status, product = await self.cache.get_or_fetch(article, _do_fetch)
            # если ждали чужой in-flight — для логов это HIT после чужого fetch
            if product is not None:
                if final_status == CacheStatus.HIT and self.last_cache_status != "HIT":
                    # waiter после single-flight
                    log.info("BrowserProvider: article=%s cache=HIT (single-flight)", article)
                    self.last_cache_status = "HIT"
                    self.last_browser_status = "SKIPPED_SINGLE_FLIGHT"
                else:
                    log.info("BrowserProvider: article=%s browser=SUCCESS", article)
                    self.last_browser_status = "SUCCESS"
                _sync_imt_root(product)
                return product

            log.info(
                "BrowserProvider: article=%s browser=FAILED → HTTP fallback",
                article,
            )
            self.last_browser_status = "FAILED"
            return None
        except Exception as exc:
            log.info(
                "BrowserProvider: article=%s browser=FAILED → HTTP fallback (%s)",
                article, _safe_err(exc),
            )
            self.last_browser_status = "FAILED"
            return None


def _safe_err(exc: BaseException) -> str:
    msg = str(exc)
    # на всякий случай вырежем типичные user:pass@
    msg = re_sub_creds(msg)
    return msg[:300]


def re_sub_creds(text: str) -> str:
    import re
    return re.sub(
        r"(?i)(://[^:/@\s]+):([^@/\s]+)@",
        r"\1:***@",
        text or "",
    )
