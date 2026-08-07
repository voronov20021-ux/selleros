"""
proxy_pool.py — умный пул прокси с failover и rate limiting.

Каждый прокси:
- Имеет собственный rate limiter (1 запрос в 10 секунд)
- Имеет собственное состояние (доступен/заблокирован)
- При 403/429 блокируется на 30 минут
- Логирует все операции

Пул:
- Автоматически переключается на следующий доступный прокси
- Если все заблокированы - возвращает ошибку
- Не делает циклических проверок
- Не делает повторных запросов
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("selleros.proxy_pool")


@dataclass
class ProxyState:
    """Состояние одного прокси"""
    
    url: str
    last_used: float = 0.0
    blocked_until: float = 0.0
    total_requests: int = 0
    total_blocks: int = 0
    
    # Rate limiting: 1 запрос в 10 секунд
    MIN_INTERVAL = 10.0
    
    # Блокировка на 30 минут при 403/429
    BLOCK_DURATION = 30 * 60  # 1800 секунд
    
    def is_available(self) -> bool:
        """Проверить доступность прокси"""
        now = time.time()
        
        # Проверка блокировки
        if self.blocked_until > now:
            return False
        
        # Проверка rate limit
        if (now - self.last_used) < self.MIN_INTERVAL:
            return False
        
        return True
    
    def seconds_until_available(self) -> float:
        """Сколько секунд до разблокировки"""
        now = time.time()
        
        # Если заблокирован
        if self.blocked_until > now:
            return self.blocked_until - now
        
        # Если rate limit
        if (now - self.last_used) < self.MIN_INTERVAL:
            return self.MIN_INTERVAL - (now - self.last_used)
        
        return 0.0
    
    def mark_used(self) -> None:
        """Отметить использование"""
        self.last_used = time.time()
        self.total_requests += 1
        
        log.debug(
            "Прокси %s использован (всего запросов: %d)",
            self._short_url(), self.total_requests
        )
    
    def mark_blocked(self, reason: str = "403/429") -> None:
        """Заблокировать прокси на 30 минут"""
        self.blocked_until = time.time() + self.BLOCK_DURATION
        self.total_blocks += 1
        
        log.warning(
            "⛔ Прокси %s ЗАБЛОКИРОВАН на %.1f мин (причина: %s, всего блокировок: %d)",
            self._short_url(),
            self.BLOCK_DURATION / 60,
            reason,
            self.total_blocks
        )
    
    def unblock(self) -> None:
        """Разблокировать прокси принудительно"""
        self.blocked_until = 0.0
        log.info("✅ Прокси %s разблокирован вручную", self._short_url())
    
    def _short_url(self) -> str:
        """Короткий URL для логов (без пароля)"""
        # Формат: http://user:pass@host:port
        try:
            parts = self.url.split("@")
            if len(parts) == 2:
                host_port = parts[1]
                return f"...@{host_port}"
            return self.url
        except:
            return self.url[:20]


class ProxyPool:
    """
    Умный пул прокси с автоматическим failover.
    
    Использование:
        proxy_pool = ProxyPool.from_env_value(WB_PROXY_URLS)
        
        # Получить следующий доступный прокси
        proxy_dict = proxy_pool.get_next_available()
        if proxy_dict is None:
            raise Exception("Все прокси заблокированы")
        
        # После успешного запроса
        proxy_pool.mark_success()
        
        # После 403/429
        proxy_pool.mark_blocked("403")
    """
    
    def __init__(self, proxies: list[str] | None = None):
        """
        Инициализация пула.
        
        Args:
            proxies: Список URL прокси в формате http://user:pass@host:port
        """
        proxy_urls = [p.strip() for p in (proxies or []) if p.strip()]
        
        self._proxies: list[ProxyState] = []
        for url in proxy_urls:
            self._proxies.append(ProxyState(url=url))
        
        self._current_index: int = 0
        self._last_used_proxy: Optional[ProxyState] = None
        
        if self._proxies:
            log.info(
                "ProxyPool инициализирован: %d прокси",
                len(self._proxies)
            )
        else:
            log.warning("ProxyPool: прокси не настроены (идем без прокси)")
    
    def __bool__(self) -> bool:
        """Есть ли прокси в пуле"""
        return bool(self._proxies)
    
    @property
    def proxies(self) -> list[str]:
        """Список URL прокси (публичный API, как в старой версии ProxyPool)."""
        return [p.url for p in self._proxies]
    
    def has_available(self) -> bool:
        """Есть ли доступные прокси прямо сейчас"""
        return any(p.is_available() for p in self._proxies)
    
    def get_next_available(self) -> dict[str, str] | None:
        """
        Получить следующий доступный прокси.
        
        Returns:
            {"http": url, "https": url} или None если все заблокированы
        """
        if not self._proxies:
            return None
        
        # Ищем первый доступный прокси начиная с текущего индекса
        checked = 0
        while checked < len(self._proxies):
            proxy = self._proxies[self._current_index]
            
            if proxy.is_available():
                # Нашли доступный!
                proxy.mark_used()
                self._last_used_proxy = proxy
                
                log.info(
                    "🌐 Используется прокси %s (запрос #%d)",
                    proxy._short_url(),
                    proxy.total_requests
                )
                
                # Переходим к следующему для равномерного распределения
                self._current_index = (self._current_index + 1) % len(self._proxies)
                
                return {"http": proxy.url, "https": proxy.url}
            
            # Этот прокси недоступен, пробуем следующий
            wait_time = proxy.seconds_until_available()
            log.debug(
                "Прокси %s недоступен (доступен через %.1f сек)",
                proxy._short_url(),
                wait_time
            )
            
            self._current_index = (self._current_index + 1) % len(self._proxies)
            checked += 1
        
        # Все прокси заблокированы
        self._log_all_blocked()
        return None
    
    def mark_success(self) -> None:
        """Отметить успешный запрос последнего использованного прокси"""
        if self._last_used_proxy:
            log.debug(
                "✅ Прокси %s: запрос успешен",
                self._last_used_proxy._short_url()
            )
    
    def mark_blocked(self, reason: str = "403/429") -> None:
        """
        Заблокировать последний использованный прокси на 30 минут.
        
        Args:
            reason: Причина блокировки (например "403", "429")
        """
        if self._last_used_proxy:
            self._last_used_proxy.mark_blocked(reason)
    
    def get_status(self) -> dict:
        """Получить статус всех прокси"""
        now = time.time()
        
        status = {
            "total": len(self._proxies),
            "available": 0,
            "blocked": 0,
            "rate_limited": 0,
            "proxies": []
        }
        
        for proxy in self._proxies:
            is_blocked = proxy.blocked_until > now
            is_rate_limited = (now - proxy.last_used) < proxy.MIN_INTERVAL
            is_available = proxy.is_available()
            
            if is_available:
                status["available"] += 1
            elif is_blocked:
                status["blocked"] += 1
            elif is_rate_limited:
                status["rate_limited"] += 1
            
            status["proxies"].append({
                "url": proxy._short_url(),
                "available": is_available,
                "blocked": is_blocked,
                "seconds_until_available": proxy.seconds_until_available(),
                "total_requests": proxy.total_requests,
                "total_blocks": proxy.total_blocks,
            })
        
        return status
    
    def unblock_all(self) -> None:
        """Разблокировать все прокси принудительно"""
        for proxy in self._proxies:
            if proxy.blocked_until > time.time():
                proxy.unblock()
        log.info("Все прокси разблокированы")
    
    def _log_all_blocked(self) -> None:
        """Логировать состояние когда все прокси заблокированы"""
        log.error("⛔ ВСЕ ПРОКСИ ВРЕМЕННО НЕДОСТУПНЫ!")
        
        for i, proxy in enumerate(self._proxies, 1):
            wait_time = proxy.seconds_until_available()
            log.error(
                "  %d. %s - доступен через %.1f сек (%.1f мин)",
                i,
                proxy._short_url(),
                wait_time,
                wait_time / 60
            )
    
    @classmethod
    def from_env_value(cls, raw: str) -> "ProxyPool":
        """
        Создать из значения переменной окружения.
        
        Args:
            raw: Строка формата "url1,url2,url3" или пустая
        
        Returns:
            ProxyPool с прокси или пустой пул
        """
        return cls(raw.split(",")) if raw else cls()
