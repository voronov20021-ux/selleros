"""
Разбор KnowledgeItem (Yandex Search) → SearchCandidate.

Не выдумывает price/rating/reviews. Нет поля → None (UNKNOWN).
Identity: wb:{nm_id} из URL карточки, иначе url:{normalized}.
Не использует IMT / отзывы другого SKU.
"""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import urlparse, parse_qs, urlunparse

from backend.competitor_intelligence.models import SearchCandidate

_WB_HOSTS = frozenset({
    "www.wildberries.ru", "wildberries.ru", "wb.ru", "www.wb.ru",
})
_OZON_HOSTS = frozenset({"www.ozon.ru", "ozon.ru"})
_YM_HOSTS = frozenset({"market.yandex.ru", "www.market.yandex.ru"})

_NM_RE = re.compile(
    r"(?:wildberries\.ru|wb\.ru)/catalog/(\d{4,})(?:/|$|\?)",
    re.IGNORECASE,
)
_PRICE_RE = re.compile(
    r"(?<!\d)(\d{1,3}(?:[ \u00a0]\d{3})+|\d{3,7})\s*(?:₽|руб(?:\.|лей|ля)?)",
    re.IGNORECASE,
)
_RATING_RE = re.compile(
    r"(?:рейтинг|★|⭐)\s*(\d(?:[.,]\d)?)|(\d(?:[.,]\d)?)\s*(?:из\s*5|★)",
    re.IGNORECASE,
)
_FB_RE = re.compile(
    r"(\d{1,3}(?:[ \u00a0]\d{3})+|\d{2,6})\s*отзыв",
    re.IGNORECASE,
)
_DISCOUNT_RE = re.compile(r"(?:скидк[аи]|−|-)\s*(\d{1,2})\s*%", re.IGNORECASE)


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def marketplace_of(url: str) -> str | None:
    host = _host(url)
    if host in _WB_HOSTS or host.endswith(".wildberries.ru"):
        return "wildberries"
    if host in _OZON_HOSTS:
        return "ozon"
    if host in _YM_HOSTS:
        return "yandex_market"
    if host:
        return host
    return None


def extract_nm_id(url: str) -> int | None:
    if not url:
        return None
    m = _NM_RE.search(url)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        for key in ("nm", "nmId", "nm_id"):
            if qs.get(key):
                return int(qs[key][0])
    except (TypeError, ValueError):
        return None
    return None


def normalize_url(url: str) -> str:
    if not url:
        return ""
    try:
        p = urlparse(url.strip())
        path = p.path.rstrip("/")
        return urlunparse((p.scheme.lower(), p.netloc.lower(), path, "", "", ""))
    except Exception:
        return url.strip()


def competitor_identity(*, url: str, nm_id: int | None = None) -> str:
    if nm_id is not None:
        return f"wb:{int(nm_id)}"
    parsed_nm = extract_nm_id(url)
    if parsed_nm is not None:
        return f"wb:{parsed_nm}"
    norm = normalize_url(url)
    return f"url:{norm or url}"


def _parse_price(text: str) -> int | None:
    if not text:
        return None
    m = _PRICE_RE.search(text)
    if not m:
        return None
    raw = m.group(1).replace(" ", "").replace("\u00a0", "")
    try:
        n = int(raw)
    except ValueError:
        return None
    if n < 50 or n > 5_000_000:
        return None
    if 1990 <= n <= 2035:
        return None
    return n


def _parse_rating(text: str) -> float | None:
    if not text:
        return None
    m = _RATING_RE.search(text)
    if not m:
        return None
    raw = m.group(1) or m.group(2)
    if not raw:
        return None
    try:
        val = float(raw.replace(",", "."))
    except ValueError:
        return None
    if 0.0 <= val <= 5.0:
        return val
    return None


def _parse_feedbacks(text: str) -> int | None:
    if not text:
        return None
    m = _FB_RE.search(text)
    if not m:
        return None
    raw = m.group(1).replace(" ", "").replace("\u00a0", "")
    try:
        n = int(raw)
    except ValueError:
        return None
    return n if n >= 0 else None


def _parse_discount(text: str) -> int | None:
    if not text:
        return None
    m = _DISCOUNT_RE.search(text)
    if not m:
        return None
    try:
        n = int(m.group(1))
    except ValueError:
        return None
    return n if 0 < n <= 95 else None


def knowledge_item_to_candidate(item: Any, *, now: float | None = None) -> SearchCandidate | None:
    meta = getattr(item, "metadata", None) or {}
    if not isinstance(meta, dict):
        meta = {}
    url = (
        getattr(item, "source_url", None)
        or meta.get("url")
        or ""
    )
    url = str(url).strip()
    if not url:
        return None
    title = meta.get("title")
    if not title:
        content = getattr(item, "content", None) or ""
        if isinstance(content, str) and content.startswith("Заголовок:"):
            title = content.split("\n", 1)[0].replace("Заголовок:", "", 1).strip()
    if isinstance(title, str):
        title = title.strip() or None
    else:
        title = None
    snippet = meta.get("snippet") or ""
    content = getattr(item, "content", None) or ""
    blob = f"{title or ''} {snippet} {content}"
    nm_id = extract_nm_id(url)
    retrieved = now if now is not None else time.time()
    collected = getattr(item, "collected_at", None)
    published = getattr(item, "published_at", None)
    return SearchCandidate(
        competitor_id=competitor_identity(url=url, nm_id=nm_id),
        source=str(getattr(item, "source_id", None) or "yandex_search"),
        source_url=url,
        title=title,
        brand=None,
        category=getattr(item, "category", None),
        price=_parse_price(blob),
        rating=_parse_rating(blob),
        feedbacks=_parse_feedbacks(blob),
        discount=_parse_discount(blob),
        photo_count=None,
        characteristics=None,
        description=snippet or None,
        available_positioning=None,
        matched_attributes=[],
        confidence=float(getattr(item, "confidence", 0.7) or 0.7),
        retrieved_at=float(retrieved),
        source_timestamp=float(published) if published else (
            float(collected) if collected else None
        ),
        marketplace=marketplace_of(url),
        nm_id=nm_id,
        snippet=str(snippet)[:300] if snippet else None,
    )


def parse_search_items(items: list[Any], *, now: float | None = None) -> list[SearchCandidate]:
    out: list[SearchCandidate] = []
    seen: set[str] = set()
    ts = now if now is not None else time.time()
    for item in items or []:
        cand = knowledge_item_to_candidate(item, now=ts)
        if cand is None:
            continue
        if cand.competitor_id in seen:
            continue
        seen.add(cand.competitor_id)
        out.append(cand)
    return out
