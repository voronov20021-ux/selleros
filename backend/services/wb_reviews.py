"""
wb_reviews.py — controlled fetch текстов отзывов WB + process cache.

Endpoint (уже используемый WB feedbacks host):
    GET https://feedbacks1.wb.ru/feedbacks/v1/{imtId}

Ограничения:
    - максимум 1 HTTP на cache miss артикула;
    - без retry-loop (retries=1);
    - WBRateGate + ProxyPool как у CDNSource;
    - 403/429/5xx/ошибка → [] (graceful);
    - REVIEW_CACHE_TTL_DAYS = 7;
    - не выдумывает отзывы / счётчики.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

log = logging.getLogger("selleros.wb_reviews")

REVIEW_CACHE_TTL_DAYS = 7

try:
    from backend.config import MAX_REVIEW_TEXTS as _DEFAULT_LIMIT
except Exception:  # pragma: no cover — config may fail without BOT_TOKEN in isolation
    _DEFAULT_LIMIT = 60

_FEEDBACK_HOSTS = ("feedbacks1.wb.ru", "feedbacks2.wb.ru")


@dataclass
class Review:
    """Минимальная структура отзыва для Session / Review Intelligence."""

    review_id: str
    article_id: int
    text: str
    rating: float | None = None
    created_at: float | None = None
    source_url: str | None = None
    fingerprint: str = ""
    metadata: dict = field(default_factory=dict)

    def to_ri_dict(self) -> dict[str, Any]:
        """Payload для ReviewIntelligence.analyze (без raw user_id)."""
        return {
            "id": self.review_id,
            "review_id": self.review_id,
            "text": self.text,
            "source_url": self.source_url,
            "content": self.text,
        }


def review_fingerprint(text: str) -> str:
    norm = (text or "").lower().strip()
    norm = norm.replace("ё", "е")
    norm = re.sub(r"\s+", " ", norm)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:24]


def _compose_text(item: dict) -> str:
    """Склеиваем только реальные поля text/pros/cons — без выдумок."""
    parts: list[str] = []
    for key in ("text", "pros", "cons"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(val.strip())
    return "\n".join(parts).strip()


def parse_feedbacks_payload(
    payload: dict | None,
    *,
    article_id: int,
    imt_id: int | None,
    limit: int = _DEFAULT_LIMIT,
) -> list[Review]:
    """Разобрать JSON feedbacks*.wb.ru → list[Review]. Пустое/null → [].

    Если у отзывов есть nmId — оставляем только matching article_id
    (не смешиваем IMT-wide ленту в nm-specific Product reviews_loaded).
    """
    if not isinstance(payload, dict):
        return []
    raw_list = payload.get("feedbacks")
    if raw_list is None:
        return []
    if not isinstance(raw_list, list):
        return []

    article_id = int(article_id)
    # Есть ли хоть один nmId в ленте? Если да — фильтруем строго.
    has_nm_tags = False
    for item in raw_list:
        if isinstance(item, dict) and (
            item.get("nmId") is not None or item.get("nm_id") is not None
        ):
            has_nm_tags = True
            break

    out: list[Review] = []
    seen_fp: set[str] = set()
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        if has_nm_tags:
            nm = item.get("nmId")
            if nm is None:
                nm = item.get("nm_id")
            try:
                if nm is None or int(nm) != article_id:
                    continue
            except (TypeError, ValueError):
                continue
        text = _compose_text(item)
        if len(text) < 3:
            continue
        fp = review_fingerprint(text)
        if fp in seen_fp:
            continue
        seen_fp.add(fp)
        rid = str(item.get("id") or item.get("feedbackId") or fp)
        rating = item.get("productValuation")
        try:
            rating_f = float(rating) if rating is not None else None
        except (TypeError, ValueError):
            rating_f = None
        created = item.get("createdDate") or item.get("created")
        created_ts: float | None = None
        if isinstance(created, (int, float)):
            created_ts = float(created)
        elif isinstance(created, str) and created:
            # ISO-ish — не падаем, просто None
            try:
                from datetime import datetime
                created_ts = datetime.fromisoformat(
                    created.replace("Z", "+00:00")
                ).timestamp()
            except Exception:
                created_ts = None
        src = None
        if imt_id is not None:
            src = f"https://feedbacks1.wb.ru/feedbacks/v1/{imt_id}"
        out.append(Review(
            review_id=rid,
            article_id=article_id,
            text=text,
            rating=rating_f,
            created_at=created_ts,
            source_url=src,
            fingerprint=fp,
        ))
        if len(out) >= max(1, int(limit)):
            break
    return out


class WBReviewsService:
    """
    Controlled WB review fetcher.

    transport — опциональный async callable(url) -> (status_code, json|None)
                для тестов (mock). Если None — curl_cffi через ProxyPool.
    """

    def __init__(
        self,
        proxy_pool=None,
        *,
        transport: Callable[[str], Awaitable[tuple[int, dict | None]]] | None = None,
        ttl_days: float = REVIEW_CACHE_TTL_DAYS,
        timeout: float = 12.0,
        public_reviews_cache=None,
    ) -> None:
        self._proxy_pool = proxy_pool
        self._transport = transport
        self._ttl = float(ttl_days) * 86400.0
        self._timeout = float(timeout)
        # article_id → (fetched_at, reviews)
        self._cache: dict[int, tuple[float, list[Review]]] = {}
        self.http_calls: int = 0
        #: Публичный browser reviews cache (без user_id) — до HTTP.
        self._public_reviews_cache = public_reviews_cache

    def clear_cache(self) -> None:
        self._cache.clear()

    def cache_get(self, article_id: int) -> list[Review] | None:
        """None = miss/stale; list (в т.ч. []) = hit."""
        row = self._cache.get(int(article_id))
        if row is None:
            return None
        ts, reviews = row
        if time.time() - ts > self._ttl:
            return None
        return list(reviews)

    def cache_set(self, article_id: int, reviews: list[Review]) -> None:
        self._cache[int(article_id)] = (time.time(), list(reviews))

    def force_stale(self, article_id: int) -> None:
        """Тестовый хелпер: пометить cache как просроченный."""
        row = self._cache.get(int(article_id))
        if row is None:
            return
        _, reviews = row
        self._cache[int(article_id)] = (time.time() - self._ttl - 1.0, reviews)

    async def fetch_reviews(
        self,
        article_id: int,
        *,
        imt_id: int | None = None,
        limit: int = _DEFAULT_LIMIT,
    ) -> list[Review]:
        """
        Получить отзывы товара.
        Порядок: process cache → public browser reviews cache → HTTP (≤1).
        """
        article_id = int(article_id)
        cached = self.cache_get(article_id)
        if cached is not None:
            return cached[: max(1, int(limit))]

        # Public browser reviews (общий для продавцов, без seller memory)
        if self._public_reviews_cache is not None:
            try:
                pub = self._public_reviews_cache.get_fresh(article_id)
            except Exception:
                pub = None
            if pub is not None:
                log.info(
                    "WBReviews: article=%s public_reviews_cache=HIT n=%s",
                    article_id, len(pub),
                )
                self.cache_set(article_id, pub)
                return list(pub)[: max(1, int(limit))]

        if imt_id is None:
            log.info(
                "WBReviews: article=%s без imt_id — HTTP не делаем, возвращаем []",
                article_id,
            )
            self.cache_set(article_id, [])
            return []

        reviews = await self._http_once(article_id=article_id, imt_id=int(imt_id), limit=limit)
        self.cache_set(article_id, reviews)
        if self._public_reviews_cache is not None and reviews:
            try:
                self._public_reviews_cache.set_reviews(
                    article_id, reviews, imt_id=imt_id,
                )
            except Exception as exc:
                log.debug("public reviews cache set skip: %s", exc)
        return list(reviews)

    async def load_into_session(
        self,
        session,
        user_id: int,
        product,
        *,
        limit: int = _DEFAULT_LIMIT,
    ) -> list[Review]:
        """
        Pipeline helper: session miss → fetch (cache/HTTP) → set_product_reviews.
        Не затирает seller price/rating/feedbacks.
        """
        article = getattr(product, "article", None)
        if article is None:
            return []
        article = int(article)

        if hasattr(session, "get_product_reviews"):
            existing = session.get_product_reviews(user_id, article)
            # None = ещё не загружали; [] = загружали, пусто
            if existing is not None:
                return list(existing)

        imt = getattr(product, "imt_id", None) or getattr(product, "root_id", None)
        try:
            imt_i = int(imt) if imt is not None else None
        except (TypeError, ValueError):
            imt_i = None

        try:
            reviews = await self.fetch_reviews(article, imt_id=imt_i, limit=limit)
        except Exception as exc:
            log.warning("WBReviews load_into_session failed: %s", exc)
            reviews = []

        if hasattr(session, "set_product_reviews"):
            session.set_product_reviews(user_id, article, reviews)
        return list(reviews)

    async def _http_once(
        self,
        *,
        article_id: int,
        imt_id: int,
        limit: int,
    ) -> list[Review]:
        from backend.wb_engine.rate_gate import wb_rate_gate

        if not await wb_rate_gate.try_acquire():
            log.info(
                "WBReviews: rate gate отказал article=%s — без HTTP, []",
                article_id,
            )
            return []

        # Один controlled HTTP: пробуем host1; при None-ответе без второго
        # «живого» запроса — только если transport mock / первая попытка
        # вернула None из-за сети. Чтобы не раздувать HTTP, берём РОВНО
        # один URL (feedbacks1). feedbacks2 — только если transport сам
        # не считает (не используем цикл hosts в production path).
        url = f"https://{_FEEDBACK_HOSTS[0]}/feedbacks/v1/{imt_id}"

        try:
            status, payload = await self._do_get(url)
        except Exception as exc:
            log.warning("WBReviews: HTTP error article=%s status=exc", article_id)
            log.debug("WBReviews detail: %s", type(exc).__name__)
            return []

        self.http_calls += 1

        if status in (403, 429) or status >= 500 or status == 0:
            log.warning(
                "WBReviews: graceful empty article=%s http_status=%s",
                article_id, status,
            )
            return []
        if status != 200 or payload is None:
            log.info(
                "WBReviews: empty/non-200 article=%s http_status=%s",
                article_id, status,
            )
            return []

        return parse_feedbacks_payload(
            payload, article_id=article_id, imt_id=imt_id, limit=limit,
        )

    async def _do_get(self, url: str) -> tuple[int, dict | None]:
        if self._transport is not None:
            return await self._transport(url)

        # Production transport: curl_cffi, retries=1, timeout обязателен.
        from curl_cffi import requests as curl_requests

        proxies = None
        if self._proxy_pool is not None:
            pool_urls = getattr(self._proxy_pool, "proxies", None) or []
            if pool_urls:
                proxies = self._proxy_pool.get_next_available()
                if proxies is None:
                    log.info("WBReviews: нет доступного прокси — []")
                    return 0, None
            # пустой пул → direct (как CDN при отсутствии прокси)

        try:
            async with curl_requests.AsyncSession(
                impersonate="chrome124",
                proxies=proxies or {},
            ) as session:
                response = await session.get(url, timeout=self._timeout)
        except Exception:
            if self._proxy_pool is not None:
                try:
                    self._proxy_pool.mark_blocked("transport")
                except Exception:
                    pass
            raise

        status = int(getattr(response, "status_code", 0) or 0)
        if status == 403 or status == 429:
            if self._proxy_pool is not None:
                try:
                    self._proxy_pool.mark_blocked(str(status))
                except Exception:
                    pass
            return status, None
        if status != 200:
            return status, None

        if self._proxy_pool is not None:
            try:
                self._proxy_pool.mark_success()
            except Exception:
                pass

        try:
            payload = response.json()
        except Exception:
            return status, None
        if not isinstance(payload, dict):
            return status, None
        return status, payload
