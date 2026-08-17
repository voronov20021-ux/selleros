"""Thin catalog / public-analyze helpers for Mini App.

Calls existing ProductService + MemoryStore. Does not invent assortment,
profit, or market numbers.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Optional

from backend.competitor_intelligence.parser import extract_nm_id

log = logging.getLogger("selleros.api.miniapp_catalog")

_DIGITS = re.compile(r"^\d{4,}$")
_NM_IN_TEXT = re.compile(
    r"(?:wildberries\.ru|wb\.ru)/catalog/(\d{4,})|[?&](?:nm|nmId|nm_id)=(\d{4,})",
    re.IGNORECASE,
)
#: Mixed phrases like «Разбери 357657814». 7+ digits avoids prices like 300.
_STANDALONE_NM = re.compile(r"(?<!\d)(\d{7,12})(?!\d)")


def parse_article_input(raw: str | int | None) -> Optional[int]:
    if raw is None:
        return None
    if isinstance(raw, int):
        return int(raw) if raw > 0 else None
    text = str(raw).strip()
    if not text:
        return None
    if _DIGITS.match(text):
        return int(text)
    nm = extract_nm_id(text)
    if nm:
        return int(nm)
    m = _NM_IN_TEXT.search(text)
    if m:
        return int(m.group(1) or m.group(2))
    standalone = _STANDALONE_NM.search(text)
    if standalone:
        return int(standalone.group(1))
    return None


def first_photo(product: Any) -> Optional[str]:
    photos = getattr(product, "photos", None)
    if isinstance(photos, (list, tuple)):
        for item in photos:
            if isinstance(item, str) and item.startswith("http"):
                return item
    image = getattr(product, "image", None)
    if isinstance(image, str) and image.startswith("http"):
        return image
    return None


def photo_count(product: Any) -> int:
    photos = getattr(product, "photos", None)
    if isinstance(photos, (list, tuple)):
        return len(photos)
    n = getattr(product, "photo_count", None)
    if n is None and isinstance(photos, int):
        n = photos
    try:
        return int(n or 0)
    except (TypeError, ValueError):
        return 0


def serialize_memory_product(row: Any, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    article = int(row.article)
    extra = (meta or {}).get(str(article)) or (meta or {}).get(article) or {}
    score = getattr(row, "score", None)
    try:
        score_i = int(score) if score is not None else None
    except (TypeError, ValueError):
        score_i = None
    if score_i is None:
        status = None
    elif score_i >= 75:
        status = "GREEN"
    elif score_i >= 50:
        status = "YELLOW"
    else:
        status = "RED"
    return {
        "article": article,
        "title": getattr(row, "title", None) or str(article),
        "image": extra.get("image"),
        "price": getattr(row, "price", None),
        "rating": getattr(row, "rating", None),
        "feedback_count": getattr(row, "feedbacks", None),
        "reviews_count": getattr(row, "feedbacks", None),
        "argus_score": score_i,
        "argus_status": status,
        "problems": [],
        "recommendations": [],
        "demo": False,
        "owned": True,
        "updated_at": extra.get("updated_at") or getattr(row, "last_seen", None),
        "seller_data": seller_fields(row),
    }


SELLER_KEYS = (
    "ctr",
    "cvr",
    "impressions",
    "views",
    "sales",
    "orders",
    "returns",
    "ad_spend",
    "cost",
    "commission",
    "logistics",
    "storage",
    "period",
)


def seller_fields(row: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in SELLER_KEYS:
        out[key] = getattr(row, key, None)
    return out


def product_payload(product: Any, article: int) -> dict[str, Any]:
    return {
        "article": int(getattr(product, "article", article) or article),
        "title": getattr(product, "title", None) or getattr(product, "name", None) or str(article),
        "image": first_photo(product),
        "price": getattr(product, "price", None),
        "rating": getattr(product, "rating", None),
        "feedback_count": getattr(product, "reviews", None) or getattr(product, "feedbacks", None),
        "brand": getattr(product, "brand", None) or "",
        "source": getattr(product, "source", None),
        "demo": False,
    }


async def ready_memory(memory) -> Any:
    if memory is None:
        return None
    if getattr(memory, "_db", None) is None:
        await memory.connect()
    return memory


async def upsert_seller_product(memory, seller_id: int, product: Any, article: int) -> None:
    photos = photo_count(product)
    await memory.upsert_product(
        int(seller_id),
        int(article),
        "wildberries",
        title=getattr(product, "title", None) or getattr(product, "name", None) or "",
        price=getattr(product, "price", None),
        rating=getattr(product, "rating", None),
        score=None,
        photos=photos,
        imt_id=getattr(product, "imt_id", None),
        root_id=getattr(product, "root_id", None),
    )


def remember_image(prefs_store, seller_id: str, article: int, image: Optional[str]) -> dict[str, Any]:
    prefs = prefs_store.get(seller_id)
    meta = dict(prefs.get("catalog_meta") or {})
    key = str(int(article))
    entry = dict(meta.get(key) or {})
    if image:
        entry["image"] = image
    entry["updated_at"] = time.time()
    meta[key] = entry
    return prefs_store.upsert(seller_id, catalog_meta=meta)
