"""
search_fallback.py — источник №2: поиск по номеру артикула через умный ProxyPool.

Изменения:
- Использует новый ProxyPool с failover
- Один запрос без retries
- При 403/429 помечает прокси как заблокированный
- При успехе помечает запрос как успешный
- Перед запросом к search.wb.ru проходит через общий WBRateGate —
  минимум 10 сек между ЛЮБЫМИ запросами к *.wb.ru от любого источника
"""

from __future__ import annotations

import logging

from curl_cffi import requests

from backend.wb.cdn_provider import DEFAULT_HEADERS, WBProduct, apply_detail
from backend.wb_engine.errors import SourceBlocked, SourceUnavailable
from backend.wb_engine.proxy_pool import ProxyPool
from backend.wb_engine.rate_gate import wb_rate_gate
from backend.wb_engine.source import DataSource

log = logging.getLogger("selleros.wb_engine.search")

SEARCH_URL = "https://search.wb.ru/exactmatch/ru/common/v18/search"


class SearchFallbackSource(DataSource):

    name = "search"

    def __init__(self, proxy_pool: ProxyPool | None = None, timeout: float = 15.0):
        self.proxy_pool = proxy_pool or ProxyPool()
        self.timeout = timeout

    async def fetch(self, article: int) -> WBProduct | None:
        # Общий лимит: не чаще одного запроса к *.wb.ru за 10 сек,
        # независимо от того, кто отправляет — CDN, Search или Feedback.
        # Если слот занят — не трогаем прокси вообще, просто не идём в сеть.
        if not await wb_rate_gate.try_acquire():
            raise SourceUnavailable("WB rate gate: слишком рано для нового запроса к *.wb.ru")

        # Получаем следующий доступный прокси
        proxies = None
        if self.proxy_pool:
            proxies = self.proxy_pool.get_next_available()
            if proxies is None:
                # Все прокси заблокированы
                raise SourceUnavailable("Все прокси временно недоступны")
        
        params = {
            "appType": "1",
            "curr": "rub",
            "dest": "-1257786",
            "lang": "ru",
            "page": "1",
            "query": str(article),
            "resultset": "catalog",
            "sort": "popular",
            "spp": "30",
        }

        try:
            async with requests.AsyncSession(
                headers=DEFAULT_HEADERS,
                impersonate="chrome124",
                proxies=proxies or {},
                timeout=self.timeout,
            ) as session:
                response = await session.get(SEARCH_URL, params=params)
        except Exception as error:
            log.warning("Search: сетевая ошибка %s", error)
            raise SourceUnavailable(str(error)) from error

        # Проверяем статус
        if response.status_code == 403:
            log.warning("Search: получен 403 Forbidden")
            if self.proxy_pool:
                self.proxy_pool.mark_blocked("403")
            raise SourceBlocked("search.wb.ru вернул 403")
        
        if response.status_code == 429:
            log.warning("Search: получен 429 Too Many Requests")
            if self.proxy_pool:
                self.proxy_pool.mark_blocked("429")
            raise SourceBlocked("search.wb.ru вернул 429")

        if response.status_code != 200:
            log.warning("Search: статус %s", response.status_code)
            raise SourceUnavailable(f"search.wb.ru -> {response.status_code}")

        try:
            products = response.json().get("products") or []
        except ValueError as error:
            raise SourceUnavailable(f"невалидный JSON: {error}") from error

        # Берём только точное совпадение по артикулу
        raw = next((p for p in products if int(p.get("id", -1)) == article), None)

        if raw is None:
            # Товар не найден в поиске - это не ошибка прокси
            return None

        # Успех!
        product = WBProduct(article=article)
        apply_detail(product, raw)
        product.title = product.title or raw.get("name")
        product.source = "live"
        
        # Помечаем прокси как рабочий
        if self.proxy_pool:
            self.proxy_pool.mark_success()

        return product
