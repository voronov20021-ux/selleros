"""
search_service.py — оркестрация поиска и сохранения результатов.

Единый путь к Yandex HTTP:

    Caller → SearchService → CostGuard → YandexSearchAdapter.fetch()

Без CostGuard (cost_guard=None) production HTTP ЗАПРЕЩЁН,
если явно не передан allow_unguarded=True (только тесты/диагностика).
"""

from __future__ import annotations

import logging

from backend.intelligence.evidence.engine import EvidenceEngine
from backend.intelligence.interfaces import IIntelligenceStore
from backend.intelligence.models import KnowledgeItem
from backend.intelligence.sources.yandex_search import YandexSearchAdapter

log = logging.getLogger("selleros.intelligence.search_service")


class SearchService:
    """
    Координирует поиск через CostGuard → YandexSearchAdapter → store.

    Использование (production):
        svc = SearchService(
            store=store, engine=engine, adapter=adapter, cost_guard=guard,
        )
        items = await svc.search_and_store(query="мужские часы", category="Часы")
    """

    def __init__(
        self,
        store: IIntelligenceStore,
        engine: EvidenceEngine,
        adapter: YandexSearchAdapter | None = None,
        cost_guard=None,
        *,
        allow_unguarded: bool = False,
    ) -> None:
        self._store = store
        self._engine = engine
        self._adapter = adapter or YandexSearchAdapter()
        self._guard = cost_guard
        self._allow_unguarded = allow_unguarded

    @property
    def cost_guard(self):
        return self._guard

    @property
    def is_guarded(self) -> bool:
        """True если CostGuard подключён (production-safe)."""
        return self._guard is not None

    async def search_and_store(
        self,
        query: str,
        category: str | None = None,
        region: str = "RU",
    ) -> list[KnowledgeItem]:
        """
        Поиск с обязательным CostGuard boundary.

        - CACHE_HIT / CACHED_LIMIT → HTTP=0, вернуть кэш из store
        - RATE_LIMITED → HTTP=0, вернуть [] (или пустой кэш)
        - ALLOWED → 1 HTTP fetch + record
        - cost_guard=None и allow_unguarded=False → HTTP=0 (fail-closed)
        """
        if self._guard is not None:
            result = await self._guard.check(query, category, region)

            if result.from_cache:
                log.info(
                    "SearchService: CostGuard %s для %r — HTTP пропущен",
                    result.status.value, query,
                )
                return await self._load_cached(query)

            if not result.http_allowed:
                log.warning(
                    "SearchService: CostGuard %s (%d/%d) — HTTP запрещён для %r",
                    result.status.value,
                    result.requests_used,
                    result.requests_limit,
                    query,
                )
                return []

            items = await self._fetch_and_persist(query, category, region)
            if items is not None:
                # record только после успешного HTTP (даже если 0 docs — вызов был)
                await self._guard.record(query, category, region)
            return items or []

        # Нет CostGuard
        if not self._allow_unguarded:
            log.error(
                "SearchService: cost_guard=None — HTTP заблокирован "
                "(fail-closed). Передайте cost_guard или allow_unguarded=True."
            )
            return await self._load_cached(query)

        log.warning(
            "SearchService: unguarded HTTP для %r (allow_unguarded=True)", query,
        )
        return await self._fetch_and_persist(query, category, region) or []

    async def ensure_source_registered(self) -> None:
        """Зарегистрировать DataSource 'yandex_search' в store (upsert)."""
        data_source = self._adapter.to_data_source()
        await self._store.save_source(data_source)
        log.debug("YandexSearch DataSource зарегистрирован в store.")

    # ──────────────────────────── internal ──────────────────────────────── #

    async def _fetch_and_persist(
        self,
        query: str,
        category: str | None,
        region: str,
    ) -> list[KnowledgeItem]:
        items = await self._adapter.fetch(
            query=query,
            category=category,
            region=region,
        )
        if not items:
            log.info("SearchService: пустой результат для %r", query)
            return []

        saved = 0
        evidence_count = 0
        for item in items:
            try:
                await self._store.save_item(item)
                saved += 1
            except Exception as exc:
                log.warning(
                    "SearchService: не удалось сохранить item %s: %s",
                    item.id, exc,
                )
                continue
            try:
                await self._engine.ingest(item)
                evidence_count += 1
            except Exception as exc:
                log.warning(
                    "SearchService: не удалось создать Evidence для %s: %s",
                    item.id, exc,
                )

        log.info(
            "SearchService: %r → %d items сохранено, %d Evidence создано",
            query, saved, evidence_count,
        )
        return items

    async def _load_cached(self, query: str) -> list[KnowledgeItem]:
        """Вернуть свежие KnowledgeItem из store по metadata.query."""
        try:
            items = await self._store.search_items_by_query(
                query=query,
                source_id="yandex_search",
                since_ts=None,
                limit=50,
            )
            return items
        except Exception as exc:
            log.warning("SearchService: не удалось загрузить кэш для %r: %s", query, exc)
            return []
