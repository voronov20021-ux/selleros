"""Сериализация публичных полей WBProduct для общего browser-кэша."""

from __future__ import annotations

from typing import Any

from backend.wb.cdn_provider import WBProduct, _sync_imt_root


#: Только публичные WB-поля. Seller price/rating/notes — НЕ сюда.
_PUBLIC_FIELDS = (
    "article", "title", "brand", "brand_id", "description", "vendor_code",
    "subject_name", "subject_root_name", "characteristics", "composition",
    "colors", "price", "old_price", "discount", "wallet_price", "rating",
    "feedbacks", "supplier", "supplier_id", "supplier_rating",
    "imt_id", "root_id", "basket", "photo_count", "photos", "video",
    "total_qty", "is_promo", "source", "field_provenance",
)


def product_to_public_dict(product: WBProduct) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for name in _PUBLIC_FIELDS:
        val = getattr(product, name, None)
        if name == "characteristics" and val is None:
            val = {}
        if name in ("composition", "colors", "photos") and val is None:
            val = []
        data[name] = val
    data["kind"] = "product"
    return data


def product_from_public_dict(data: dict[str, Any]) -> WBProduct:
    kwargs: dict[str, Any] = {}
    for name in _PUBLIC_FIELDS:
        if name in data:
            kwargs[name] = data[name]
    if "article" not in kwargs or kwargs["article"] is None:
        raise ValueError("public cache entry without article")
    kwargs["article"] = int(kwargs["article"])
    product = WBProduct(**{
        k: v for k, v in kwargs.items()
        if k in WBProduct.__dataclass_fields__
    })
    _sync_imt_root(product)
    if not product.source:
        product.source = "browser_cache"
    return product
