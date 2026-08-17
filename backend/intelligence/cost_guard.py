"""
cost_guard.py — защитный лимит вызовов Yandex Search API.

YandexCostGuard обеспечивает два уровня защиты:

1. Кэширование (CACHE_TTL_DAYS):
   Если для данного (query, category, region) уже есть свежие KnowledgeItem
   в store — HTTP-запрос пропускается, возвращаются кэшированные данные.

2. Лимит запросов (LIMIT / WINDOW_DAYS):
   Максимум LIMIT реальных HTTP-вызовов за скользящее окно WINDOW_DAYS дней.
   После достижения лимита новые HTTP-запросы запрещены.
   Кэш при этом продолжает работать: cached hits не блокируются.

Счётчик хранится в IntelligenceStore (таблица api_calls).
При превышении лимита — возвращает controlled empty result + статус.

Константы вынесены наверх: LIMIT, WINDOW_DAYS, CACHE_TTL_DAYS.
Меняете 60 → 200 или 7 → 1 — в одном месте.

ВАЖНО: единственный production boundary — SearchService → CostGuard → HTTP.
CategoryIntelligence не должен самостоятельно обходить/дублировать запись счётчика.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from enum import Enum

from backend.intelligence.interfaces import IIntelligenceStore

log = logging.getLogger("selleros.intelligence.cost_guard")

# ─────────────────────── конфигурационные константы ───────────────────── #

#: Максимум реальных HTTP-запросов за скользящее окно.
LIMIT: int = 60

#: Ширина скользящего окна в днях.
WINDOW_DAYS: int = 7

#: TTL кэша в днях. Повторный запрос того же query/category/region
#: не чаще одного раза за CACHE_TTL_DAYS дней.
CACHE_TTL_DAYS: int = 7

#: Source ID для счётчика (в таблице api_calls).
_GUARD_SOURCE_ID: str = "yandex_search"

_WINDOW_SECONDS: float = WINDOW_DAYS * 86400.0
_CACHE_TTL_SECONDS: float = CACHE_TTL_DAYS * 86400.0


# ──────────────────────────── статус проверки ────────────────────────── #


class GuardStatus(str, Enum):
    CACHE_HIT    = "cache_hit"       # данные в кэше, HTTP не нужен
    ALLOWED      = "allowed"         # лимит не исчерпан, HTTP разрешён
    RATE_LIMITED = "rate_limited"    # лимит исчерпан, кэша нет
    CACHED_LIMIT = "cached_limit"    # лимит исчерпан, но кэш доступен


@dataclass
class GuardResult:
    status: GuardStatus
    http_allowed: bool    # True = можно делать HTTP-запрос
    from_cache: bool      # True = есть кэшированные данные
    requests_used: int    # текущий счётчик за окно
    requests_limit: int   # лимит


class YandexCostGuard:
    """
    Защитный лимит вызовов Yandex Search API.

    Использование (внутри SearchService.search_and_store):

        result = await guard.check(query, category, region)
        if result.from_cache:
            return cached_items
        elif result.http_allowed:
            items = await adapter.fetch(...)
            await guard.record(query, category, region)
        else:
            return []  # RATE_LIMITED

    Setup — один раз после store.connect():
        await guard.setup()   # CREATE TABLE IF NOT EXISTS (идемпотентно)
    """

    def __init__(self, store: IIntelligenceStore) -> None:
        self._store = store

    async def setup(self) -> None:
        """
        Идемпотентная инициализация.
        Таблица api_calls создаётся через schema.sql при connect(),
        поэтому этот метод сейчас является no-op, но сохраняется
        для явной инициализации в коде вызывающего.
        """
        # Таблица api_calls уже создаётся schema.sql.
        # setup() оставлен для возможности будущей миграции.
        log.debug("YandexCostGuard.setup(): таблица api_calls готова (schema.sql)")

    # ─────────────────────────── проверка ──────────────────────────────── #

    async def check(
        self,
        query: str,
        category: str | None,
        region: str,
    ) -> GuardResult:
        """
        Проверить: можно ли делать HTTP-запрос?

        1. Если кэш свежий → CACHE_HIT (http_allowed=False, from_cache=True).
        2. Если лимит не исчерпан → ALLOWED (http_allowed=True).
        3. Если лимит исчерпан и кэш есть → CACHED_LIMIT (http_allowed=False, from_cache=True).
        4. Если лимит исчерпан и кэша нет → RATE_LIMITED (http_allowed=False, from_cache=False).
        """
        used = await self._count_used()
        cached = await self._is_cached(query, category, region)

        if cached:
            status = GuardStatus.CACHE_HIT
            return GuardResult(
                status=status,
                http_allowed=False,
                from_cache=True,
                requests_used=used,
                requests_limit=LIMIT,
            )

        if used < LIMIT:
            return GuardResult(
                status=GuardStatus.ALLOWED,
                http_allowed=True,
                from_cache=False,
                requests_used=used,
                requests_limit=LIMIT,
            )

        # Лимит исчерпан
        status = GuardStatus.CACHED_LIMIT if cached else GuardStatus.RATE_LIMITED
        return GuardResult(
            status=status,
            http_allowed=False,
            from_cache=cached,
            requests_used=used,
            requests_limit=LIMIT,
        )

    async def record(
        self,
        query: str,
        category: str | None,
        region: str,
        source_id: str = _GUARD_SOURCE_ID,
    ) -> None:
        """Записать факт реального HTTP-вызова. Вызывать ПОСЛЕ успешного fetch()."""
        await self._store.record_api_call(
            call_id=str(uuid.uuid4()),
            source_id=source_id,
            query=query,
            category=category,
            region=region,
            called_at=time.time(),
        )
        used = await self._count_used()
        log.info(
            "YandexCostGuard: HTTP call recorded. Использовано %d/%d за %d дней.",
            used, LIMIT, WINDOW_DAYS,
        )

    async def usage(self) -> tuple[int, int]:
        """Вернуть (использовано, лимит) за текущее окно."""
        return (await self._count_used(), LIMIT)

    # ─────────────────────────── internal ──────────────────────────────── #

    async def _count_used(self) -> int:
        since = time.time() - _WINDOW_SECONDS
        return await self._store.count_api_calls(_GUARD_SOURCE_ID, since)

    async def _is_cached(
        self,
        query: str,
        category: str | None,
        region: str,
    ) -> bool:
        """
        Проверить наличие свежих KnowledgeItem для данного query.
        Считается «свежим», если collected_at >= now - CACHE_TTL_SECONDS.
        """
        since = time.time() - _CACHE_TTL_SECONDS
        items = await self._store.search_items_by_query(
            query=query,
            source_id=_GUARD_SOURCE_ID,
            since_ts=since,
            limit=1,
        )
        return bool(items)
