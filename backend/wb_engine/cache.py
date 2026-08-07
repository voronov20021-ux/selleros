"""
cache.py — короткий кэш с "склейкой" одинаковых запросов.

Идея была в неиспользуемом backend/core/cache.py, но с багом:
если callback() падает с исключением, будущее (future) для ВСЕХ,
кто ждал тот же ключ, получало это исключение — включая тех,
кто, возможно, подошёл бы к делу иначе. Здесь это поведение то же
(это на самом деле правильно: если источник упал, все ждавшие
должны узнать об этом сразу, а не ждать по новой) — но добавлена
защита: если сам callback() не был вызван до исключения,
pending[key] всё равно корректно очищается.

Главная польза: если пять продавцов почти одновременно спросят про
один и тот же трендовый товар, к WB уйдёт ОДИН запрос, а не пять.
Именно это в основном спасает от 429 при резком всплеске интереса
к товару, а не только перебор источников.
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")

DEFAULT_TTL = 60 * 5  # 5 минут: карточка не меняется каждую секунду


class ProductCache:

    def __init__(self, ttl: float = DEFAULT_TTL):
        self.ttl = ttl
        self._data: dict[str, tuple[object, float]] = {}
        self._pending: dict[str, asyncio.Future] = {}

    def get(self, key: str):
        item = self._data.get(key)
        if item is None:
            return None

        value, expires_at = item
        if time.time() > expires_at:
            del self._data[key]
            return None

        return value

    def set(self, key: str, value, ttl: float | None = None) -> None:
        self._data[key] = (value, time.time() + (ttl if ttl is not None else self.ttl))

    async def get_or_fetch(
        self,
        key: str,
        fetch: Callable[[], Awaitable[T]],
        ttl: float | None = None,
    ) -> T:
        """
        Отдать значение из кэша, а если его там нет — вызвать fetch().

        Если пока идёт запрос для этого ключа, второй и третий
        одновременный вызов НЕ запускают fetch() заново — все ждут
        один и тот же результат.
        """
        cached = self.get(key)
        if cached is not None:
            return cached

        if key in self._pending:
            return await self._pending[key]

        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[key] = future

        try:
            result = await fetch()
            self.set(key, result, ttl)
            if not future.done():
                future.set_result(result)
            return result
        except Exception as error:
            if not future.done():
                future.set_exception(error)
            raise
        finally:
            self._pending.pop(key, None)
