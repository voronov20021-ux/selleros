"""
Минимальная data-provenance для nm-specific snapshot.

Правило: One Product = one nm_id.
Не смешивать IMT-wide aggregates в nm-поля (price/rating/feedbacks).

Source abstraction (labels only — AUTH_BROWSER не реализуется здесь):
  PUBLIC_BROWSER — публичный browser/card fallback
  AUTH_BROWSER   — зарезервировано под будущую авторизованную сессию
"""

from __future__ import annotations

import time
from typing import Any

#: Абстракция источника коммерции (не второй браузер).
PUBLIC_BROWSER = "PUBLIC_BROWSER"
AUTH_BROWSER = "AUTH_BROWSER"  # stub label only — не симулировать данные


def note_field(
    product: Any,
    field: str,
    value: Any,
    source: str,
    *,
    nm_id: int | None = None,
    verified: bool = True,
    scope: str = "nm",
    cache: str | None = None,
) -> None:
    """Записать provenance на product.field_provenance (если атрибут есть)."""
    if value in (None, "", [], {}):
        return
    prov = getattr(product, "field_provenance", None)
    if prov is None:
        try:
            product.field_provenance = {}
            prov = product.field_provenance
        except Exception:
            return
    if not isinstance(prov, dict):
        return
    article = nm_id
    if article is None:
        article = getattr(product, "article", None)
    prov[field] = {
        "value": value,
        "source": source,
        "nm_id": int(article) if article is not None else None,
        "verified": bool(verified),
        "scope": scope,
        "cache": cache,
        "ts": time.time(),
    }


def same_nm(base: Any, extra: Any) -> bool:
    """True только если оба объекта относятся к одному nm_id."""
    try:
        a = getattr(base, "article", None)
        b = getattr(extra, "article", None)
        if a is None or b is None:
            return False
        return int(a) == int(b)
    except (TypeError, ValueError):
        return False


def raw_nm_id(raw: Any) -> int | None:
    if not isinstance(raw, dict):
        return None
    for key in ("id", "nmId", "nm_id", "article"):
        val = raw.get(key)
        if val is None or isinstance(val, bool):
            continue
        try:
            n = int(val)
        except (TypeError, ValueError):
            continue
        if n > 0:
            return n
    return None


def is_browser_source(product: Any) -> bool:
    src = (getattr(product, "source", None) or "").strip().lower()
    return src in ("browser", "browser_cache")


def is_nm_verified(product: Any, field: str) -> bool:
    prov = getattr(product, "field_provenance", None) or {}
    meta = prov.get(field) if isinstance(prov, dict) else None
    if not isinstance(meta, dict):
        return False
    if not (bool(meta.get("verified")) and meta.get("scope", "nm") == "nm"):
        return False
    # verified_nm_id должен совпадать с article, если записан
    article = getattr(product, "article", None)
    nm = meta.get("nm_id")
    if article is not None and nm is not None:
        try:
            if int(nm) != int(article):
                return False
        except (TypeError, ValueError):
            return False
    return True


def source_abstraction(raw_source: str | None) -> str:
    """
    Свести технический source к PUBLIC_BROWSER / AUTH_BROWSER / raw.
    AUTH_BROWSER сейчас не используется — только метка на будущее.
    """
    src = (raw_source or "").strip().lower()
    if not src:
        return ""
    if src.startswith("auth") or "authenticated" in src:
        return AUTH_BROWSER
    if (
        src.startswith("browser")
        or src in ("live", "cdn", "card", "search", "wb", "detail")
        or "card.wb" in src
        or "basket" in src
    ):
        return PUBLIC_BROWSER
    return raw_source or ""


def field_provenance_label(product: Any, field: str) -> str:
    """
    Человекочитаемый источник CARD-поля для отчёта/контекста Argus.
    Пример: «PUBLIC_BROWSER · verified nm_id» / «Browser · verified nm_id».
    """
    val = getattr(product, field, None)
    if val in (None, "", [], {}):
        return ""
    prov = getattr(product, "field_provenance", None) or {}
    meta = prov.get(field) if isinstance(prov, dict) else None
    if not isinstance(meta, dict):
        src = getattr(product, "source", None) or ""
        abs_src = source_abstraction(src) or src or "card"
        return str(abs_src)

    raw = str(meta.get("source") or getattr(product, "source", None) or "")
    abs_src = source_abstraction(raw) or raw or "card"
    verified = bool(meta.get("verified")) and meta.get("scope", "nm") == "nm"
    article = getattr(product, "article", None)
    nm = meta.get("nm_id")
    nm_ok = False
    if article is not None and nm is not None:
        try:
            nm_ok = int(nm) == int(article)
        except (TypeError, ValueError):
            nm_ok = False
    if verified and nm_ok:
        return f"{abs_src} · verified nm_id"
    if verified:
        return f"{abs_src} · verified"
    return str(abs_src)


def is_unverified_or_imt(product: Any, field: str) -> bool:
    """Поле есть, но provenance говорит IMT / unverified (или provenance нет у browser)."""
    val = getattr(product, field, None)
    if val in (None, "", [], {}):
        return False
    prov = getattr(product, "field_provenance", None) or {}
    meta = prov.get(field) if isinstance(prov, dict) else None
    if isinstance(meta, dict):
        if meta.get("verified") is False:
            return True
        if meta.get("scope") == "imt":
            return True
        return False
    # browser/cache без provenance — коммерцию считаем требующей verify
    return is_browser_source(product)


def collect_feedback_nm_ids(payload: Any) -> set[int]:
    """Собрать nmId из feedbacks payload (top-level + items)."""
    out: set[int] = set()
    if not isinstance(payload, dict):
        return out
    for key in ("nmId", "nm_id"):
        try:
            n = int(payload[key])
            if n > 0:
                out.add(n)
        except (KeyError, TypeError, ValueError):
            pass
    for item in payload.get("feedbacks") or []:
        if not isinstance(item, dict):
            continue
        for key in ("nmId", "nm_id", "productId"):
            try:
                n = int(item[key])
                if n > 0:
                    out.add(n)
                    break
            except (KeyError, TypeError, ValueError):
                continue
    return out


def feedbacks_meta_nm_safe(payload: Any, article: int) -> bool:
    """
    Можно ли брать IMT valuation/feedbackCount как nm-метрики карточки.

    Только если payload доказывает ownership ровно этого nm_id
    (все nmId == article). Иначе — False (не мержить).
    """
    nm_ids = collect_feedback_nm_ids(payload)
    if not nm_ids:
        return False
    return nm_ids == {int(article)}


def looks_sitewide_description(text: str | None) -> bool:
    """og:description / homepage-ish текст — не описание карточки."""
    if not text or not isinstance(text, str):
        return True
    t = text.strip().lower()
    if len(t) < 20:
        return True
    markers = (
        "интернет-магазин wildberries",
        "широкий ассортимент",
        "wildberries —",
        "вайлдберриз",
        "бесплатная доставка по всей россии",
    )
    return any(m in t for m in markers)


def canonical_photo_count(product: Any) -> int:
    """photo_count канонический; len(DOM photos) — только fallback если count пуст."""
    pc = getattr(product, "photo_count", None)
    try:
        if pc is not None and int(pc) > 0:
            return int(pc)
    except (TypeError, ValueError):
        pass
    photos = getattr(product, "photos", None) or []
    return len(photos) if isinstance(photos, list) else 0
