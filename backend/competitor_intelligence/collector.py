"""
CompetitorCollector — поиск кандидатов через search.wb.ru.

Не трогает WBEngine internals: только тот же URL/параметры, что уже
используют ProductCardProvider / SearchFallbackSource, через ProxyPool
и WBRateGate (ждём слот, не долбим SOCKS).
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from backend.competitor_intelligence.models import CompetitorCandidate

log = logging.getLogger("selleros.competitor_intelligence.collector")

SEARCH_URL = "https://search.wb.ru/exactmatch/ru/common/v18/search"
DEFAULT_DEST = -1257786

_STOPWORDS = frozenset({
    "для", "и", "или", "на", "в", "с", "со", "по", "от", "из", "к", "ко",
    "а", "но", "the", "and", "or", "of", "to", "a", "an", "шт", "см", "мм",
    "набор", "комплект", "новый", "новая", "новое",
})


async def await_wb_rate_slot(*, label: str = "competitor") -> None:
    """Дождаться окна WBRateGate перед *.wb.ru запросом (sleep снаружи)."""
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
        log.info("CompetitorCollector: wait %.1fs (%s)", delay, label)
        await asyncio.sleep(delay)


def _positive_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _nonneg_int(value: Any) -> int | None:
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


def _extract_price_rub(raw: dict[str, Any]) -> int | None:
    for size in raw.get("sizes") or []:
        if not isinstance(size, dict):
            continue
        block = size.get("price") or {}
        if not isinstance(block, dict):
            continue
        final = block.get("product") or block.get("total")
        try:
            kopecks = int(final)
        except (TypeError, ValueError):
            continue
        if kopecks > 0:
            return kopecks // 100
    for key in ("salePriceU", "priceU"):
        try:
            kopecks = int(raw.get(key))
        except (TypeError, ValueError):
            continue
        if kopecks > 0:
            return kopecks // 100
    return None


def _extract_products(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
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


def raw_to_candidate(raw: dict[str, Any], *, query: str | None = None) -> CompetitorCandidate | None:
    article = _positive_int(raw.get("id") or raw.get("nmId") or raw.get("nm_id"))
    if article is None:
        return None
    title = raw.get("name") or raw.get("title")
    if isinstance(title, str):
        title = title.strip() or None
    else:
        title = None
    rating = _float_or_none(
        raw.get("reviewRating") or raw.get("nmReviewRating") or raw.get("rating")
    )
    feedbacks = _nonneg_int(raw.get("feedbacks") or raw.get("nmFeedbacks"))
    photo_count = _nonneg_int(raw.get("pics") or raw.get("photo_count"))
    category_id = _positive_int(raw.get("subjectId") or raw.get("subject_id"))
    category_name = raw.get("subjectName") or raw.get("subject_name")
    if isinstance(category_name, str):
        category_name = category_name.strip() or None
    else:
        category_name = None
    brand = raw.get("brand")
    if isinstance(brand, str):
        brand = brand.strip() or None
    else:
        brand = None
    return CompetitorCandidate(
        article=article,
        title=title,
        price=_extract_price_rub(raw),
        rating=rating,
        feedbacks=feedbacks,
        category_id=category_id,
        category_name=category_name,
        brand=brand,
        photo_count=photo_count,
        query=query,
        raw=dict(raw),
    )


def keywords_from_title(title: str | None, *, max_words: int = 6) -> list[str]:
    if not title or not str(title).strip():
        return []
    tokens = re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", str(title).lower())
    words: list[str] = []
    for tok in tokens:
        if len(tok) < 2:
            continue
        if tok in _STOPWORDS:
            continue
        if tok.isdigit() and len(tok) < 3:
            continue
        words.append(tok)
        if len(words) >= max_words:
            break
    return words


def build_search_queries(
    *,
    title: str | None = None,
    brand: str | None = None,
    category_name: str | None = None,
    extra_keywords: list[str] | None = None,
) -> list[str]:
    """Несколько запросов: полный title → keywords → brand+category."""
    queries: list[str] = []
    seen: set[str] = set()

    def add(q: str | None) -> None:
        text = (q or "").strip()
        if not text:
            return
        key = text.lower()
        if key in seen:
            return
        seen.add(key)
        queries.append(text)

    add(title)
    kws = keywords_from_title(title)
    if kws:
        add(" ".join(kws[:4]))
        if len(kws) >= 2:
            add(" ".join(kws[:2]))
    if brand and kws:
        add(f"{brand} {kws[0]}")
    if category_name and brand:
        add(f"{brand} {category_name}")
    elif category_name:
        add(category_name)
    for extra in extra_keywords or []:
        add(extra)
    return queries


class CompetitorCollector:
    """
    Собирает nm_id кандидатов из search.wb.ru по title / keywords / category.
    """

    def __init__(
        self,
        *,
        proxy_pool: Any = None,
        dest: int = DEFAULT_DEST,
        timeout: float = 20.0,
        max_queries: int = 2,
        max_products_per_query: int = 40,
        respect_wb_rate_gate: bool = True,
        allow_direct_fallback: bool = True,
        proxy_wait_budget: float = 12.0,
        throttle_retry_wait: float = 35.0,
    ) -> None:
        self.proxy_pool = proxy_pool
        self.dest = dest
        self.timeout = timeout
        self.max_queries = max(1, int(max_queries))
        self.max_products_per_query = max(5, int(max_products_per_query))
        self.respect_wb_rate_gate = respect_wb_rate_gate
        self.allow_direct_fallback = allow_direct_fallback
        self.proxy_wait_budget = max(0.0, float(proxy_wait_budget))
        self.throttle_retry_wait = max(0.0, float(throttle_retry_wait))

    def _search_params(self, query: str) -> dict[str, str]:
        return {
            "appType": "1",
            "curr": "rub",
            "dest": str(self.dest),
            "lang": "ru",
            "page": "1",
            "query": query,
            "resultset": "catalog",
            "sort": "popular",
            "spp": "30",
        }

    async def _acquire_rate_slot(self) -> bool:
        if not self.respect_wb_rate_gate:
            return True
        try:
            from backend.wb_engine.rate_gate import wb_rate_gate
        except Exception:
            return True
        await await_wb_rate_slot(label="search")
        try:
            ok = await wb_rate_gate.try_acquire()
        except Exception:
            return True
        if not ok:
            # ещё раз мягко подождать и повторить один раз
            await asyncio.sleep(1.0)
            try:
                return bool(await wb_rate_gate.try_acquire())
            except Exception:
                return True
        return True

    async def _resolve_proxies(self) -> dict[str, str] | None:
        """
        Взять ProxyPool слот. Если прокси только на коротком MIN_INTERVAL —
        подождать до proxy_wait_budget. При 30-мин block → None (direct fallback).
        """
        pool = self.proxy_pool
        if pool is None:
            return None
        try:
            proxies = pool.get_next_available()
        except Exception as exc:
            log.warning("CompetitorCollector: proxy_pool error: %s", exc)
            return None
        if proxies is not None:
            return proxies

        # Короткий cooldown (rate limit), не 30-мин блок
        min_wait = None
        try:
            for p in getattr(pool, "_proxies", []) or []:
                blocked_until = float(getattr(p, "blocked_until", 0.0) or 0.0)
                import time as _time
                now = _time.time()
                if blocked_until > now:
                    continue
                w = float(p.seconds_until_available())
                if min_wait is None or w < min_wait:
                    min_wait = w
        except Exception:
            min_wait = None

        if min_wait is not None and 0 < min_wait <= self.proxy_wait_budget:
            log.info(
                "CompetitorCollector: wait %.1fs for proxy rate window",
                min_wait,
            )
            await asyncio.sleep(min_wait + 0.05)
            try:
                return pool.get_next_available()
            except Exception:
                return None
        return None

    async def _http_get_search(
        self,
        query: str,
        *,
        proxies: dict[str, str] | None,
    ) -> tuple[int | None, Any]:
        try:
            from curl_cffi import requests
            from backend.wb.cdn_provider import DEFAULT_HEADERS
        except Exception as exc:
            log.warning("CompetitorCollector: import failed: %s", exc)
            return None, None

        try:
            async with requests.AsyncSession(
                headers=DEFAULT_HEADERS,
                impersonate="chrome124",
                proxies=proxies or {},
                timeout=self.timeout,
            ) as session:
                response = await session.get(
                    SEARCH_URL, params=self._search_params(query),
                )
        except Exception as exc:
            log.warning(
                "CompetitorCollector: network error query=%r proxy=%s: %s",
                query,
                "yes" if proxies else "direct",
                exc,
            )
            return None, None
        return getattr(response, "status_code", None), response

    def _forced_proxy_dict(self) -> dict[str, str] | None:
        """URL прокси без проверки block/rate — только для throttle-retry."""
        pool = self.proxy_pool
        if pool is None:
            return None
        urls = getattr(pool, "proxies", None) or []
        if not urls:
            return None
        url = urls[0]
        return {"http": url, "https": url}

    async def _parse_search_response(
        self,
        status: int | None,
        response: Any,
        *,
        query: str,
        used_proxy: bool,
    ) -> list[CompetitorCandidate]:
        log.info(
            "COMPETITOR SEARCH\nquery=%r\nstatus=%s\nproxy=%s",
            query,
            status,
            "yes" if used_proxy else "direct",
        )
        if status != 200 or response is None:
            return []
        try:
            payload = response.json()
        except Exception:
            return []
        if used_proxy and self.proxy_pool is not None:
            try:
                self.proxy_pool.mark_success()
            except Exception:
                pass
        out: list[CompetitorCandidate] = []
        for raw in _extract_products(payload)[: self.max_products_per_query]:
            cand = raw_to_candidate(raw, query=query)
            if cand is not None:
                out.append(cand)
        return out

    async def search_query(self, query: str) -> list[CompetitorCandidate]:
        query = (query or "").strip()
        if not query:
            return []

        if not await self._acquire_rate_slot():
            log.warning("CompetitorCollector: rate gate busy, skip query=%r", query)
            return []

        proxies = await self._resolve_proxies()
        used_proxy = bool(proxies)
        if (
            proxies is None
            and self.proxy_pool is not None
            and getattr(self.proxy_pool, "proxies", None)
            and not self.allow_direct_fallback
        ):
            log.warning("CompetitorCollector: no available proxy for query=%r", query)
            return []

        if proxies is None and self.proxy_pool is not None and self.allow_direct_fallback:
            log.info(
                "CompetitorCollector: proxy unavailable → direct search query=%r",
                query,
            )

        status, response = await self._http_get_search(query, proxies=proxies)

        # Если через прокси 403/429 — mark_blocked + retry direct.
        if status in (403, 429) and used_proxy:
            if self.proxy_pool is not None:
                try:
                    self.proxy_pool.mark_blocked(str(status))
                except Exception:
                    pass
            if self.allow_direct_fallback:
                log.info(
                    "CompetitorCollector: proxy %s → retry direct query=%r",
                    status,
                    query,
                )
                if self.respect_wb_rate_gate:
                    await await_wb_rate_slot(label="search-direct-retry")
                    try:
                        from backend.wb_engine.rate_gate import wb_rate_gate
                        await wb_rate_gate.try_acquire()
                    except Exception:
                        pass
                status, response = await self._http_get_search(query, proxies=None)
                used_proxy = False

        rows = await self._parse_search_response(
            status, response, query=query, used_proxy=used_proxy,
        )
        if rows:
            return rows

        # Throttle recovery: WB часто отдаёт 429 на direct IP после серии CDN.
        # Ждём и один раз пробуем forced proxy (игнорируя 30-мин block).
        if status in (403, 429, None) or not rows:
            wait = self.throttle_retry_wait
            if wait > 0:
                log.info(
                    "CompetitorCollector: throttle empty/status=%s → wait %.0fs then forced proxy",
                    status,
                    wait,
                )
                await asyncio.sleep(wait)
                if self.respect_wb_rate_gate:
                    await await_wb_rate_slot(label="search-throttle-retry")
                    try:
                        from backend.wb_engine.rate_gate import wb_rate_gate
                        await wb_rate_gate.try_acquire()
                    except Exception:
                        pass
                forced = self._forced_proxy_dict()
                status2, response2 = await self._http_get_search(
                    query, proxies=forced,
                )
                rows2 = await self._parse_search_response(
                    status2,
                    response2,
                    query=query,
                    used_proxy=bool(forced),
                )
                if rows2:
                    return rows2
                # последний шанс — снова direct после паузы
                if forced is not None and self.allow_direct_fallback:
                    if self.respect_wb_rate_gate:
                        await await_wb_rate_slot(label="search-final-direct")
                        try:
                            from backend.wb_engine.rate_gate import wb_rate_gate
                            await wb_rate_gate.try_acquire()
                        except Exception:
                            pass
                    status3, response3 = await self._http_get_search(
                        query, proxies=None,
                    )
                    return await self._parse_search_response(
                        status3, response3, query=query, used_proxy=False,
                    )
        return rows

    async def collect(
        self,
        *,
        title: str | None = None,
        brand: str | None = None,
        category_name: str | None = None,
        exclude_article: int | None = None,
        extra_keywords: list[str] | None = None,
    ) -> list[CompetitorCandidate]:
        queries = build_search_queries(
            title=title,
            brand=brand,
            category_name=category_name,
            extra_keywords=extra_keywords,
        )[: self.max_queries]

        by_article: dict[int, CompetitorCandidate] = {}
        for query in queries:
            for cand in await self.search_query(query):
                if exclude_article is not None and cand.article == int(exclude_article):
                    continue
                prev = by_article.get(cand.article)
                if prev is None:
                    by_article[cand.article] = cand
                elif (prev.price is None) and (cand.price is not None):
                    by_article[cand.article] = cand

        result = list(by_article.values())
        log.info(
            "CompetitorCollector: queries=%d candidates=%d exclude=%s",
            len(queries),
            len(result),
            exclude_article,
        )
        return result

    async def collect_from_product_context(
        self,
        product_context: Any,
        *,
        extra_keywords: list[str] | None = None,
    ) -> list[CompetitorCandidate]:
        product = getattr(product_context, "product", None)
        article = getattr(product, "article", None) if product is not None else None
        title = getattr(product, "title", None) if product is not None else None
        brand = getattr(product, "brand", None) if product is not None else None
        # category may live on last_api_product; optional attrs on context
        category_name = getattr(product_context, "category_name", None)
        return await self.collect(
            title=title,
            brand=brand,
            category_name=category_name,
            exclude_article=int(article) if article is not None else None,
            extra_keywords=extra_keywords,
        )
