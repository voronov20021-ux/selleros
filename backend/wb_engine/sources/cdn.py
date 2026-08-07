"""
cdn.py — источник №1: CDN + Detail API через умный ProxyPool.

Изменения:
- Использует новый ProxyPool с failover
- Один запрос без retries (retries в AsyncWBClient отключены)
- При 403/429 помечает прокси как заблокированный
- При успехе помечает запрос как успешный
- Перед запросом к card.wb.ru проходит через общий WBRateGate —
  минимум 10 сек между ЛЮБЫМИ запросами к *.wb.ru от любого источника
"""

from __future__ import annotations

import logging

from backend.wb.cdn_provider import AsyncWBClient, WBProduct
from backend.wb_engine.errors import SourceBlocked, SourceUnavailable
from backend.wb_engine.proxy_pool import ProxyPool
from backend.wb_engine.rate_gate import wb_rate_gate
from backend.wb_engine.source import DataSource

log = logging.getLogger("selleros.wb_engine.cdn")


class CDNSource(DataSource):

    name = "cdn"

    def __init__(self, proxy_pool: ProxyPool | None = None, basket_cache: str | None = None):
        self.proxy_pool = proxy_pool or ProxyPool()
        self.basket_cache = basket_cache

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
        
        try:
            async with AsyncWBClient(
                basket_cache=self.basket_cache,
                proxies=proxies,
                retries=1,  # Только одна попытка
                timeout=15.0,
            ) as client:
                product = await client.scan(article)

            if product is not None:
                product.source = "live"
                
                # Успех - помечаем прокси как рабочий
                if self.proxy_pool:
                    self.proxy_pool.mark_success()
            
            return product
            
        except Exception as error:
            # Проверяем код ошибки
            error_str = str(error).lower()
            
            if "403" in error_str or "forbidden" in error_str:
                log.warning("CDN: получен 403 Forbidden")
                if self.proxy_pool:
                    self.proxy_pool.mark_blocked("403")
                raise SourceBlocked("CDN вернул 403")
            
            elif "429" in error_str or "too many" in error_str:
                log.warning("CDN: получен 429 Too Many Requests")
                if self.proxy_pool:
                    self.proxy_pool.mark_blocked("429")
                raise SourceBlocked("CDN вернул 429")
            
            else:
                # Другая ошибка - не блокируем прокси
                log.warning("CDN: ошибка %s", error)
                raise SourceUnavailable(str(error)) from error

