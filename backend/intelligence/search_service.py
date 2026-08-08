"""
search_service.py — оркестрация поиска и сохранения результатов.

SearchService — тонкий координирующий слой между адаптером и хранилищем.
Он не содержит бизнес-логики; только вызывает:

    adapter.fetch()       → list[KnowledgeItem]
    store.save_item()     → сохраняет каждый item
    engine.ingest()       → item → Evidence (с нормализацией и confidence)

Почему отдельный сервис, а не метод адаптера?
    Адаптер по контракту (DataSourceAdapter.fetch) не знает про store.
    Класть store в конструктор адаптера нарушало бы SRP и затрудняло
    тестирование. SearchService — чистый "use case" без побочных эффектов
    на существующую архитектуру.
"""

from __future__ import annotations

import logging

from backend.intelligence.evidence.engine import EvidenceEngine
from backend.intelligence.interfaces import IIntelligenceStore
from backend.intelligence.models import Evidence, KnowledgeItem
from backend.intelligence.sources.yandex_search import YandexSearchAdapter

log = logging.getLogger("selleros.intelligence.search_service")


class SearchService:
    """
    Координирует поиск через YandexSearchAdapter → хранение в IntelligenceStore.

    Использование:
        svc = SearchService(store=intel_store, engine=evidence_engine)
        items = await svc.search_and_store(
            query="мужские часы",
            category="Часы",
        )
    """

    def __init__(
        self,
        store: IIntelligenceStore,
        engine: EvidenceEngine,
        adapter: YandexSearchAdapter | None = None,
    ) -> None:
        self._store = store
        self._engine = engine
        self._adapter = adapter or YandexSearchAdapter()

    async def search_and_store(
        self,
        query: str,
        category: str | None = None,
        region: str = "RU",
    ) -> list[KnowledgeItem]:
        """
        Выполнить поиск, сохранить KnowledgeItem и создать Evidence.

        Порядок действий:
            1. fetch()  — получить сырые KnowledgeItem из Yandex Search
            2. save_item() — сохранить каждый item в knowledge_items
            3. ingest()  — item → Evidence (нормализация, confidence, save)

        Возвращает список KnowledgeItem (те же объекты, что вернул fetch).
        Evidence создаются как побочный эффект — доступны через retrieve().

        При ошибке fetch() пробрасывает SourceUnavailableError.
        Ошибки сохранения отдельного item логируются и не прерывают весь цикл.
        """
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
                    item.id,
                    exc,
                )
                continue

            try:
                await self._engine.ingest(item)
                evidence_count += 1
            except Exception as exc:
                log.warning(
                    "SearchService: не удалось создать Evidence для %s: %s",
                    item.id,
                    exc,
                )

        log.info(
            "SearchService: %r → %d items сохранено, %d Evidence создано",
            query,
            saved,
            evidence_count,
        )

        return items

    async def ensure_source_registered(self) -> None:
        """
        Зарегистрировать DataSource 'yandex_search' в store (upsert).

        Вызывать при инициализации, до первого search_and_store().
        Идемпотентно: повторный вызов только обновляет метаданные.
        """
        data_source = self._adapter.to_data_source()
        await self._store.save_source(data_source)
        log.debug("YandexSearch DataSource зарегистрирован в store.")
