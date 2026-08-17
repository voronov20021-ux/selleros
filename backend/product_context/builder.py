"""
ProductContextBuilder — сборка единого ProductContext из нескольких источников.

Порядок:
  1) public/product cache (read)
  2) ProductService / WBEngine API path
  3) CDN images (из уже полученного продукта или отдельный partial)
  4) Reviews (WBReviews) — после паузы WBRateGate (10s), без ломки gate
  5) Browser fallback ТОЛЬКО если нет description / characteristics /
     supplier / imt_id
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.product_context.models import (
    ProductContext,
    ProductDescription,
    ProductIdentity,
    ProductMedia,
    ProductPricing,
    ProductReviews,
)
from backend.product_context import sources as src

log = logging.getLogger("selleros.product_context.builder")

_BROWSER_KEYS = ("description", "characteristics", "supplier", "imt_id")


async def _await_wb_rate_slot() -> None:
    """
    Дождаться окна WBRateGate перед следующим *.wb.ru запросом.
    Не меняет rate_gate / ProxyPool — только спит снаружи.
    """
    try:
        from backend.wb_engine.rate_gate import wb_rate_gate
    except Exception:
        return
    try:
        elapsed = float(wb_rate_gate.seconds_since_last())
        need = float(getattr(wb_rate_gate, "MIN_INTERVAL", 10.0))
    except Exception:
        return
    if elapsed < need:
        delay = need - elapsed + 0.05
        log.info(
            "ProductContextBuilder: wait %.1fs for WB rate gate before reviews",
            delay,
        )
        await asyncio.sleep(delay)


def _merge_partial(
    fields: dict[str, Any],
    sources: dict[str, str],
    partial: dict[str, Any],
    partial_sources: dict[str, str],
    *,
    overwrite: bool = False,
) -> None:
    """Заполнить только пустые поля (или overwrite=True для browser fill)."""
    for key, value in partial.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, dict)) and not value:
            continue

        current = fields.get(key)
        empty = (
            current is None
            or (isinstance(current, str) and not current.strip())
            or (isinstance(current, (list, dict)) and not current)
        )
        if empty or overwrite:
            if overwrite and not empty and key not in _BROWSER_KEYS:
                continue
            if overwrite and not empty:
                continue
            fields[key] = value
            if key in partial_sources:
                sources[key] = partial_sources[key]


def _needs_browser_fallback(fields: dict[str, Any]) -> bool:
    desc = fields.get("description")
    chars = fields.get("characteristics") or {}
    supplier = fields.get("supplier")
    imt = fields.get("imt_id")
    if not (isinstance(desc, str) and desc.strip()):
        return True
    if not isinstance(chars, dict) or not chars:
        return True
    if not (isinstance(supplier, str) and supplier.strip()):
        return True
    if imt is None:
        return True
    return False


def _build_context(article: int, fields: dict[str, Any], sources: dict[str, str]) -> ProductContext:
    return ProductContext(
        product=ProductIdentity(
            article=int(fields.get("article") or article),
            imt_id=fields.get("imt_id"),
            root_id=fields.get("root_id"),
            title=fields.get("title"),
            brand=fields.get("brand"),
            supplier=fields.get("supplier"),
        ),
        pricing=ProductPricing(
            price=fields.get("price"),
            old_price=fields.get("old_price"),
            rating=fields.get("rating"),
            feedback_count=fields.get("feedback_count"),
        ),
        media=ProductMedia(
            photos=list(fields.get("photos") or []),
            photo_count=fields.get("photo_count"),
        ),
        description=ProductDescription(
            description=fields.get("description"),
            characteristics=dict(fields.get("characteristics") or {}),
            sizes=list(fields.get("sizes") or []),
        ),
        reviews=ProductReviews(
            reviews_count=fields.get("reviews_count"),
            review_texts=list(fields.get("review_texts") or []),
            review_summary=fields.get("review_summary"),
        ),
        sources=dict(sources),
    )


class ProductContextBuilder:
    """
    Собирает ProductContext для артикула.

    Зависимости опциональны и инжектятся снаружи (тесты / bot / scripts).
    """

    def __init__(
        self,
        *,
        product_service: Any = None,
        reviews_service: Any = None,
        browser_provider: Any = None,
        product_cache: Any = None,
        allow_browser_fallback: bool = True,
        reviews_limit: int | None = None,
        respect_wb_rate_gate: bool = True,
    ) -> None:
        self.product_service = product_service
        self.reviews_service = reviews_service
        self.browser_provider = browser_provider
        self.product_cache = product_cache
        self.allow_browser_fallback = allow_browser_fallback
        if reviews_limit is None:
            try:
                from backend.config import MAX_REVIEW_TEXTS
                reviews_limit = int(MAX_REVIEW_TEXTS)
            except Exception:
                reviews_limit = 60
        self.reviews_limit = max(1, int(reviews_limit))
        self.respect_wb_rate_gate = respect_wb_rate_gate
        self.last_api_product: Any = None
        self.browser_used: bool = False

    async def build(self, article: int) -> ProductContext:
        article = int(article)
        fields: dict[str, Any] = {"article": article}
        sources: dict[str, str] = {"article": "input"}
        self.last_api_product = None
        self.browser_used = False

        # 1) Cache
        cache_fields, cache_sources = await src.fetch_from_cache(
            self.product_cache, article,
        )
        _merge_partial(fields, sources, cache_fields, cache_sources)

        # 2) API / ProductService (WBEngine path) — один вызов
        api_fields, api_sources, api_product = await src.fetch_from_product_service(
            self.product_service, article,
        )
        self.last_api_product = api_product
        _merge_partial(fields, sources, api_fields, api_sources)

        # 3) CDN images
        media_fields, media_sources = src.extract_cdn_media(api_product)
        _merge_partial(fields, sources, media_fields, media_sources)
        if fields.get("photos") and sources.get("photos") in (
            src.SRC_API, "live", src.SRC_CDN,
        ):
            sources["photos"] = src.SRC_CDN
            if "photo_count" in fields:
                sources["photo_count"] = src.SRC_CDN

        # 4) Reviews
        imt = fields.get("imt_id") or fields.get("root_id")
        try:
            imt_i = int(imt) if imt is not None else None
        except (TypeError, ValueError):
            imt_i = None
        if self.reviews_service is not None and imt_i is not None:
            if self.respect_wb_rate_gate:
                await _await_wb_rate_slot()
        rev_fields, rev_sources = await src.fetch_reviews(
            self.reviews_service,
            article,
            imt_id=imt_i,
            limit=self.reviews_limit,
        )
        _merge_partial(fields, sources, rev_fields, rev_sources)

        # 5) Browser fallback — только если ProductService НЕ владеет Browser
        # (Source Fetch Policy: ≤1 Browser entry via ProductService / TTL).
        from backend.services.source_fetch_policy import (
            MSG_SKIP_DUP,
            log_policy,
            product_service_owns_browser,
        )

        needs_fallback = _needs_browser_fallback(fields)
        if (
            self.allow_browser_fallback
            and self.browser_provider is not None
            and needs_fallback
        ):
            if product_service_owns_browser(self.product_service):
                # ProductService already ran BrowserProvider (or cache HIT).
                # A second get_product here would only re-enter the same TTL
                # window — forbid duplicate Browser trigger from this layer.
                log_policy(article, MSG_SKIP_DUP, detail="builder_skip")
            else:
                log.info(
                    "ProductContextBuilder: article=%s browser fallback "
                    "(missing description/characteristics/supplier/imt)",
                    article,
                )
                br_fields, br_sources = await src.fetch_from_browser(
                    self.browser_provider, article,
                )
                if br_fields:
                    self.browser_used = True
                    for key in _BROWSER_KEYS:
                        if key not in br_fields:
                            continue
                        cur = fields.get(key)
                        empty = (
                            cur is None
                            or (isinstance(cur, str) and not str(cur).strip())
                            or (isinstance(cur, dict) and not cur)
                        )
                        if empty:
                            fields[key] = br_fields[key]
                            sources[key] = br_sources.get(key, src.SRC_BROWSER)
                    _merge_partial(
                        fields, sources, br_fields, br_sources, overwrite=False,
                    )

        return _build_context(article, fields, sources)
