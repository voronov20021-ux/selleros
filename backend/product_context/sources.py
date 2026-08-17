"""
Тонкие адаптеры источников → partial dict + source labels.

Не переписывают CDN / Browser / Reviews / WBEngine — только вызывают
существующие API и мапят в поля ProductContext.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

log = logging.getLogger("selleros.product_context.sources")

# Метки источников (как в ТЗ / live diagnostics).
SRC_CACHE = "public_cache"
SRC_API = "product_api"
SRC_CDN = "cdn"
SRC_SEARCH = "search_api"
SRC_DETAIL = "card_detail"
SRC_REVIEWS = "feedbacks1"
SRC_BROWSER = "browser"


def _positive_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _nonneg_int(value: Any) -> int | None:
    """Счётчики: 0 допустим, если пришёл с API."""
    if value is None or isinstance(value, bool):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def _float_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _sizes_to_plain(sizes: Any) -> list[Any]:
    if not sizes:
        return []
    out: list[Any] = []
    for s in sizes:
        if isinstance(s, dict):
            out.append(dict(s))
            continue
        out.append({
            "name": getattr(s, "name", None) or "",
            "orig_name": getattr(s, "orig_name", None) or "",
            "price": getattr(s, "price", None),
            "old_price": getattr(s, "old_price", None),
            "qty": getattr(s, "qty", None),
        })
    return out


def map_wb_product(
    product: Any,
    *,
    default_source: str = SRC_API,
) -> tuple[dict[str, Any], dict[str, str]]:
    """
    WBProduct / duck-typed объект → partial fields + per-field source labels.

    Пустые/отсутствующие поля не попадают в partial (чтобы merge не затирал).
    """
    if product is None:
        return {}, {}

    raw_source = _str_or_none(getattr(product, "source", None)) or default_source
    # Нормализуем известные метки WBEngine / cache.
    if raw_source in ("live", "cdn", "CDNSource", "cdn_source"):
        label = SRC_CDN
    elif raw_source in ("search", "search_fallback", "SearchFallbackSource"):
        label = SRC_SEARCH
    elif raw_source in ("browser", "browser_cache"):
        label = SRC_BROWSER if raw_source == "browser" else SRC_CACHE
    elif raw_source in ("seller_api", "SellerAPISource"):
        label = "seller_api"
    else:
        label = raw_source if raw_source != "live" else default_source

    fields: dict[str, Any] = {}
    sources: dict[str, str] = {}

    def put(key: str, value: Any, src: str | None = None) -> None:
        if value is None:
            return
        if isinstance(value, str) and not value.strip():
            return
        if isinstance(value, (list, dict)) and not value:
            return
        fields[key] = value
        sources[key] = src or label

    article = _positive_int(getattr(product, "article", None))
    if article is not None:
        put("article", article, label)

    put("imt_id", _positive_int(getattr(product, "imt_id", None)))
    put("root_id", _positive_int(getattr(product, "root_id", None)))
    put("title", _str_or_none(getattr(product, "title", None)))
    put("brand", _str_or_none(getattr(product, "brand", None)))
    put("supplier", _str_or_none(getattr(product, "supplier", None)))

    # Цена: None если нет; не пишем 0 «от себя».
    price = getattr(product, "price", None)
    if price is not None:
        try:
            price_i = int(price)
        except (TypeError, ValueError):
            price_i = None
        if price_i is not None and price_i > 0:
            put("price", price_i)

    old_price = getattr(product, "old_price", None)
    if old_price is not None:
        try:
            old_i = int(old_price)
        except (TypeError, ValueError):
            old_i = None
        if old_i is not None and old_i > 0:
            put("old_price", old_i)

    rating = _float_or_none(getattr(product, "rating", None))
    if rating is not None:
        put("rating", rating)

    feedbacks = getattr(product, "feedbacks", None)
    fb = _nonneg_int(feedbacks)
    if fb is not None:
        put("feedback_count", fb)

    photos = getattr(product, "photos", None) or []
    photo_count = getattr(product, "photo_count", None)
    pc = _nonneg_int(photo_count)
    if photos:
        put("photos", list(photos), SRC_CDN if label in (SRC_CDN, SRC_API, "live") else label)
    if pc is not None and pc > 0:
        put(
            "photo_count",
            pc,
            SRC_CDN if label in (SRC_CDN, SRC_API, "live") else label,
        )
    elif photos:
        put("photo_count", len(photos), SRC_CDN)

    desc = _str_or_none(getattr(product, "description", None))
    if desc:
        put("description", desc)

    chars = getattr(product, "characteristics", None) or {}
    if isinstance(chars, dict) and chars:
        put("characteristics", dict(chars))

    sizes = _sizes_to_plain(getattr(product, "sizes", None))
    if sizes:
        put("sizes", sizes)

    return fields, sources


async def fetch_from_cache(
    cache: Any,
    article: int,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Читает PublicProductCache (HIT only). Не пишет в кэш."""
    if cache is None:
        return {}, {}
    try:
        product = None
        if hasattr(cache, "get_fresh"):
            product = cache.get_fresh(int(article))
        if product is None and hasattr(cache, "peek_any"):
            # peek только если свежего нет — всё равно пометим как cache
            product = None  # stale не используем как primary fill
        if product is None:
            return {}, {}
        fields, sources = map_wb_product(product, default_source=SRC_CACHE)
        # Переписываем метки на public_cache
        sources = {k: SRC_CACHE for k in sources}
        return fields, sources
    except Exception as exc:
        log.debug("product_context cache skip: %s", exc)
        return {}, {}


async def fetch_from_product_service(
    product_service: Any,
    article: int,
    *,
    marketplace: str = "wildberries",
) -> tuple[dict[str, Any], dict[str, str], Any]:
    """
    ProductService / WBEngine path — без переписывания движка.

    Returns: (fields, sources, raw_product_or_None)
    """
    if product_service is None:
        return {}, {}, None
    try:
        product = await product_service.get_product(marketplace, int(article))
    except Exception as exc:
        log.warning("product_context product_service failed: %s", exc)
        return {}, {}, None
    if product is None:
        return {}, {}, None
    fields, sources = map_wb_product(product, default_source=SRC_API)
    return fields, sources, product


def extract_cdn_media(product: Any) -> tuple[dict[str, Any], dict[str, str]]:
    """
    Достать photos / photo_count из уже полученного WBProduct.
    Если photos пусты, но есть basket+photo_count — собрать URL через photo_urls().
    """
    if product is None:
        return {}, {}
    fields: dict[str, Any] = {}
    sources: dict[str, str] = {}

    photos = list(getattr(product, "photos", None) or [])
    pc = _nonneg_int(getattr(product, "photo_count", None))

    if not photos and pc and pc > 0 and hasattr(product, "photo_urls"):
        try:
            photos = list(product.photo_urls() or [])
        except Exception:
            photos = []

    if photos:
        fields["photos"] = photos
        sources["photos"] = SRC_CDN
        fields["photo_count"] = pc if pc is not None else len(photos)
        sources["photo_count"] = SRC_CDN
    elif pc is not None and pc > 0:
        fields["photo_count"] = pc
        sources["photo_count"] = SRC_CDN

    return fields, sources


async def fetch_reviews(
    reviews_service: Any,
    article: int,
    *,
    imt_id: int | None = None,
    limit: int | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """WBReviewsService → review_texts / reviews_count."""
    if reviews_service is None:
        return {}, {}
    if limit is None:
        try:
            from backend.config import MAX_REVIEW_TEXTS
            limit = int(MAX_REVIEW_TEXTS)
        except Exception:
            limit = 60
    try:
        reviews = await reviews_service.fetch_reviews(
            int(article),
            imt_id=imt_id,
            limit=limit,
        )
    except Exception as exc:
        log.warning("product_context reviews failed: %s", exc)
        return {}, {}

    if reviews is None:
        return {}, {}

    texts: list[str] = []
    for rev in reviews:
        text = getattr(rev, "text", None)
        if text is None and isinstance(rev, Mapping):
            text = rev.get("text") or rev.get("content")
        if isinstance(text, str) and text.strip():
            texts.append(text.strip())

    # Пустой ответ (rate-gate / 403 / miss) — не помечаем reviews_count=0
    # как успешный снимок; оставляем поле отсутствующим.
    if not texts:
        return {}, {}

    fields: dict[str, Any] = {
        "review_texts": texts,
        "reviews_count": len(texts),
        "review_summary": f"Загружено {len(texts)} текстов отзывов",
    }
    sources = {
        "review_texts": SRC_REVIEWS,
        "reviews_count": SRC_REVIEWS,
        "review_summary": SRC_REVIEWS,
    }
    return fields, sources


async def fetch_from_browser(
    browser_provider: Any,
    article: int,
) -> tuple[dict[str, Any], dict[str, str]]:
    """BrowserProvider.get_product — только как fallback."""
    if browser_provider is None:
        return {}, {}
    try:
        available = True
        if hasattr(browser_provider, "is_available"):
            available = await browser_provider.is_available()
        if not available:
            return {}, {}
        product = await browser_provider.get_product(int(article))
    except Exception as exc:
        log.warning("product_context browser fallback failed: %s", exc)
        return {}, {}
    if product is None:
        return {}, {}
    fields, sources = map_wb_product(product, default_source=SRC_BROWSER)
    sources = {k: SRC_BROWSER for k in sources}
    return fields, sources
