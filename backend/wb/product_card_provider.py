"""
ProductCardProvider — коммерческие поля карточки WB (цена / рейтинг /
продавец / число отзывов).

Primary:
  GET https://card.wb.ru/cards/v4/detail
    ?appType=1&curr=rub&lang=ru&dest=...&spp=30&ab_testing=false&nm={nm}

Fallback (только endpoints, уже известные проекту / CDN basket):
  1) search.wb.ru/exactmatch/.../search — query=название из card.json,
     точное совпадение id == nm_id (артикул как query даёт product-redirect
     с пустым products[])
  2) basket-XX/.../info/sellers.json — supplierName / supplierId
  3) basket-XX/.../info/price-history.json — последний известный price.RUB
  4) feedbacks1.wb.ru/feedbacks/v1/{imt} — valuation + feedbackCount
     (только счётчики карточки, не тексты отзывов / не ReviewPipeline)

Никогда не подставляет 0 / "unknown": отсутствующее поле = None.
Не смешивается с ReviewProvider / BrowserProvider.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Mapping

log = logging.getLogger("selleros.wb.product_card")

DETAIL_PRIMARY_URL = "https://card.wb.ru/cards/v4/detail"
SEARCH_URL = "https://search.wb.ru/exactmatch/ru/common/v18/search"
FEEDBACKS_URL = "https://feedbacks1.wb.ru/feedbacks/v1/{imt_id}"

DEFAULT_DEST = -1257786

SyncGetter = Callable[..., Any]
AsyncGetter = Callable[..., Awaitable[Any]]


def _sellers_url(basket: str, article: int) -> str:
    return (
        f"https://basket-{basket}.wbbasket.ru"
        f"/vol{article // 100000}/part{article // 1000}/{article}/info/sellers.json"
    )


def _price_history_url(basket: str, article: int) -> str:
    return (
        f"https://basket-{basket}.wbbasket.ru"
        f"/vol{article // 100000}/part{article // 1000}/{article}/info/price-history.json"
    )


def _log_request(
    *,
    endpoint: str,
    status: Any,
    proxy_scheme: str,
    nm_id: int | None,
    imt_id: int | None,
) -> None:
    log.info(
        "PRODUCT CARD REQUEST\n"
        "endpoint=%s\n"
        "status=%s\n"
        "proxy_scheme=%s\n"
        "nm_id=%s\n"
        "imt_id=%s",
        endpoint,
        status,
        proxy_scheme or "none",
        nm_id,
        imt_id,
    )


def _as_positive_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _detail_params(nm: int, dest: int) -> dict[str, str]:
    return {
        "appType": "1",
        "curr": "rub",
        "lang": "ru",
        "dest": str(dest),
        "spp": "30",
        "ab_testing": "false",
        "nm": str(nm),
    }


def _search_params(query: str, dest: int) -> dict[str, str]:
    return {
        "appType": "1",
        "curr": "rub",
        "dest": str(dest),
        "lang": "ru",
        "page": "1",
        "query": query,
        "resultset": "catalog",
        "sort": "popular",
        "spp": "30",
    }


def _extract_products(payload: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not payload:
        return []
    products = payload.get("products")
    if isinstance(products, list) and products:
        return [p for p in products if isinstance(p, dict)]
    data = payload.get("data")
    if isinstance(data, dict):
        inner = data.get("products")
        if isinstance(inner, list):
            return [p for p in inner if isinstance(p, dict)]
    return []


def _response_status(response: Any) -> Any:
    if response is None:
        return None
    return getattr(response, "status_code", None) or getattr(response, "status", None)


def _response_json(response: Any) -> dict[str, Any] | None:
    if response is None:
        return None
    try:
        data = response.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def raw_from_sellers(payload: Mapping[str, Any], article: int) -> dict[str, Any]:
    """sellers.json → фрагмент, совместимый с apply_detail()."""
    raw: dict[str, Any] = {"id": article}
    name = payload.get("supplierName")
    sid = _as_positive_int(payload.get("supplierId"))
    if isinstance(name, str) and name.strip():
        raw["supplier"] = name.strip()
    if sid is not None:
        raw["supplierId"] = sid
    trademark = payload.get("trademark")
    if isinstance(trademark, str) and trademark.strip():
        raw.setdefault("brand", trademark.strip())
    return raw


def raw_from_price_history(payload: Any, article: int) -> dict[str, Any] | None:
    """
    price-history.json — список {dt, price:{RUB: kopecks}}.

    НЕ используем как текущую витринную цену без верификации (card.wb.ru /
    search nm-match). Возвращает None — caller не мержит в commercial price.
    Историческая точка остаётся доступна через отдельный history API при необходимости.
    """
    log.info(
        "price-history skipped as current price (unverified) nm_id=%s points=%s",
        article,
        len(payload) if isinstance(payload, list) else 0,
    )
    return None


def raw_from_feedbacks_meta(payload: Mapping[str, Any], article: int) -> dict[str, Any]:
    """
    valuation / feedbackCount — только если payload доказывает nm ownership.

    IMT-wide aggregates при нескольких nmId в одной карточке — НЕ мержим
    в nm-specific Product.commercial fields.
    """
    from backend.wb.provenance import feedbacks_meta_nm_safe

    raw: dict[str, Any] = {"id": article}
    if not feedbacks_meta_nm_safe(payload, article):
        log.info(
            "feedbacks1 meta rejected for nm_id=%s (IMT-wide or unproven ownership)",
            article,
        )
        return raw

    valuation = payload.get("valuation")
    if valuation is not None and valuation != "":
        try:
            raw["reviewRating"] = float(valuation)
        except (TypeError, ValueError):
            pass
    count = payload.get("feedbackCount")
    if count is not None:
        try:
            n = int(count)
            if n >= 0:
                raw["feedbacks"] = n
        except (TypeError, ValueError):
            pass
    return raw


def merge_commercial(base: dict[str, Any] | None, extra: dict[str, Any] | None) -> dict[str, Any]:
    """Слить fallback-фрагменты без затирания уже найденных полей."""
    out: dict[str, Any] = dict(base or {})
    if not extra:
        return out
    for key, value in extra.items():
        if key == "sizes":
            if not out.get("sizes") and value:
                out["sizes"] = value
            continue
        if key == "id":
            out.setdefault("id", value)
            continue
        if out.get(key) in (None, "", [], {}):
            if value not in (None, "", [], {}):
                out[key] = value
    return out


def needs_commercial_fallback(product: Any) -> bool:
    """Нужен fallback, если нет цены / рейтинга / отзывов / продавца."""
    return (
        getattr(product, "price", None) is None
        or getattr(product, "rating", None) is None
        or getattr(product, "feedbacks", None) is None
        or not getattr(product, "supplier", None)
    )


class ProductCardProvider:
    """Primary card.wb.ru + graceful fallbacks для коммерческих полей."""

    def __init__(
        self,
        *,
        dest: int = DEFAULT_DEST,
        proxy_scheme: str = "socks5",
    ):
        self.dest = dest
        self.proxy_scheme = proxy_scheme or "socks5"

    # ------------------------------------------------------------------ sync

    def fetch_detail_primary(
        self,
        articles: list[int],
        getter: SyncGetter,
        *,
        json_loads: Callable[[Any], dict[str, Any] | None],
    ) -> dict[int, dict[str, Any]]:
        """Primary detail API. Пустой dict при 403/ошибке — без исключения."""
        result: dict[int, dict[str, Any]] = {}
        if not articles:
            return result
        nm = ";".join(str(a) for a in articles)
        params = _detail_params(articles[0], self.dest)
        params["nm"] = nm
        response = getter(DETAIL_PRIMARY_URL, params=params)
        status = _response_status(response) if response is not None else "failed"
        _log_request(
            endpoint=DETAIL_PRIMARY_URL,
            status=status,
            proxy_scheme=self.proxy_scheme,
            nm_id=articles[0] if len(articles) == 1 else None,
            imt_id=None,
        )
        if response is None or status != 200:
            return result
        payload = json_loads(response) or {}
        for raw in _extract_products(payload):
            try:
                result[int(raw["id"])] = raw
            except (KeyError, TypeError, ValueError):
                continue
        return result

    def enrich_fallback(
        self,
        article: int,
        getter: SyncGetter,
        *,
        json_loads: Callable[[Any], dict[str, Any] | None],
        name: str | None = None,
        basket: str | None = None,
        imt_id: int | None = None,
    ) -> dict[str, Any] | None:
        merged: dict[str, Any] = {"id": article}

        if name and name.strip():
            params = _search_params(name.strip(), self.dest)
            response = getter(SEARCH_URL, params=params)
            status = _response_status(response) if response is not None else "failed"
            _log_request(
                endpoint=SEARCH_URL,
                status=status,
                proxy_scheme=self.proxy_scheme,
                nm_id=article,
                imt_id=imt_id,
            )
            if status == 200:
                payload = json_loads(response)
                for raw in _extract_products(payload):
                    try:
                        if int(raw.get("id", -1)) == article:
                            merged = merge_commercial(merged, raw)
                            break
                    except (TypeError, ValueError):
                        continue

        if basket:
            sellers_endpoint = _sellers_url(basket, article)
            response = getter(sellers_endpoint)
            status = _response_status(response) if response is not None else "failed"
            _log_request(
                endpoint=sellers_endpoint,
                status=status,
                proxy_scheme=self.proxy_scheme,
                nm_id=article,
                imt_id=imt_id,
            )
            if status == 200:
                payload = json_loads(response)
                if payload:
                    sellers_raw = raw_from_sellers(payload, article)
                    # sellers.json — каноническое имя продавца (search часто даёт бренд)
                    if sellers_raw.get("supplier"):
                        merged["supplier"] = sellers_raw["supplier"]
                    if sellers_raw.get("supplierId"):
                        merged["supplierId"] = sellers_raw["supplierId"]
                    if sellers_raw.get("brand") and not merged.get("brand"):
                        merged["brand"] = sellers_raw["brand"]

            if merged.get("sizes") is None:
                hist_endpoint = _price_history_url(basket, article)
                response = getter(hist_endpoint)
                status = _response_status(response) if response is not None else "failed"
                _log_request(
                    endpoint=hist_endpoint,
                    status=status,
                    proxy_scheme=self.proxy_scheme,
                    nm_id=article,
                    imt_id=imt_id,
                )
                hist = None
                if response is not None and status == 200:
                    try:
                        hist = response.json()
                    except Exception:
                        hist = None
                hist_raw = raw_from_price_history(hist, article)
                if hist_raw:
                    merged = merge_commercial(merged, hist_raw)

        if imt_id and (merged.get("reviewRating") is None or merged.get("feedbacks") is None):
            fb_endpoint = FEEDBACKS_URL.format(imt_id=imt_id)
            response = getter(fb_endpoint)
            status = _response_status(response) if response is not None else "failed"
            _log_request(
                endpoint=fb_endpoint,
                status=status,
                proxy_scheme=self.proxy_scheme,
                nm_id=article,
                imt_id=imt_id,
            )
            if status == 200:
                payload = json_loads(response)
                if payload:
                    merged = merge_commercial(
                        merged, raw_from_feedbacks_meta(payload, article)
                    )

        useful = {k: v for k, v in merged.items() if k != "id" and v not in (None, "", [], {})}
        return merged if useful else None

    # ---------------------------------------------------------------- async

    async def fetch_detail_primary_async(
        self,
        articles: list[int],
        getter: AsyncGetter,
        *,
        json_loads: Callable[[Any], dict[str, Any] | None],
    ) -> dict[int, dict[str, Any]]:
        result: dict[int, dict[str, Any]] = {}
        if not articles:
            return result
        params = _detail_params(articles[0], self.dest)
        params["nm"] = ";".join(str(a) for a in articles)
        response = await getter(DETAIL_PRIMARY_URL, params=params)
        status = _response_status(response) if response is not None else "failed"
        _log_request(
            endpoint=DETAIL_PRIMARY_URL,
            status=status,
            proxy_scheme=self.proxy_scheme,
            nm_id=articles[0] if len(articles) == 1 else None,
            imt_id=None,
        )
        if response is None or status != 200:
            return result
        payload = json_loads(response) or {}
        for raw in _extract_products(payload):
            try:
                result[int(raw["id"])] = raw
            except (KeyError, TypeError, ValueError):
                continue
        return result

    async def enrich_fallback_async(
        self,
        article: int,
        getter: AsyncGetter,
        *,
        json_loads: Callable[[Any], dict[str, Any] | None],
        name: str | None = None,
        basket: str | None = None,
        imt_id: int | None = None,
    ) -> dict[str, Any] | None:
        merged: dict[str, Any] = {"id": article}

        if name and name.strip():
            params = _search_params(name.strip(), self.dest)
            response = await getter(SEARCH_URL, params=params)
            status = _response_status(response) if response is not None else "failed"
            _log_request(
                endpoint=SEARCH_URL,
                status=status,
                proxy_scheme=self.proxy_scheme,
                nm_id=article,
                imt_id=imt_id,
            )
            if status == 200:
                payload = json_loads(response)
                for raw in _extract_products(payload):
                    try:
                        if int(raw.get("id", -1)) == article:
                            merged = merge_commercial(merged, raw)
                            break
                    except (TypeError, ValueError):
                        continue

        if basket:
            sellers_endpoint = _sellers_url(basket, article)
            response = await getter(sellers_endpoint)
            status = _response_status(response) if response is not None else "failed"
            _log_request(
                endpoint=sellers_endpoint,
                status=status,
                proxy_scheme=self.proxy_scheme,
                nm_id=article,
                imt_id=imt_id,
            )
            if status == 200:
                payload = json_loads(response)
                if payload:
                    sellers_raw = raw_from_sellers(payload, article)
                    if sellers_raw.get("supplier"):
                        merged["supplier"] = sellers_raw["supplier"]
                    if sellers_raw.get("supplierId"):
                        merged["supplierId"] = sellers_raw["supplierId"]
                    if sellers_raw.get("brand") and not merged.get("brand"):
                        merged["brand"] = sellers_raw["brand"]

            if merged.get("sizes") is None:
                hist_endpoint = _price_history_url(basket, article)
                response = await getter(hist_endpoint)
                status = _response_status(response) if response is not None else "failed"
                _log_request(
                    endpoint=hist_endpoint,
                    status=status,
                    proxy_scheme=self.proxy_scheme,
                    nm_id=article,
                    imt_id=imt_id,
                )
                hist = None
                if response is not None and status == 200:
                    try:
                        hist = response.json()
                    except Exception:
                        hist = None
                hist_raw = raw_from_price_history(hist, article)
                if hist_raw:
                    merged = merge_commercial(merged, hist_raw)

        if imt_id and (merged.get("reviewRating") is None or merged.get("feedbacks") is None):
            fb_endpoint = FEEDBACKS_URL.format(imt_id=imt_id)
            response = await getter(fb_endpoint)
            status = _response_status(response) if response is not None else "failed"
            _log_request(
                endpoint=fb_endpoint,
                status=status,
                proxy_scheme=self.proxy_scheme,
                nm_id=article,
                imt_id=imt_id,
            )
            if status == 200:
                payload = json_loads(response)
                if payload:
                    merged = merge_commercial(
                        merged, raw_from_feedbacks_meta(payload, article)
                    )

        useful = {k: v for k, v in merged.items() if k != "id" and v not in (None, "", [], {})}
        return merged if useful else None
