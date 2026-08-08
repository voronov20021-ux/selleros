"""
sources/registry.py — реестр источников данных.

SourceRegistry — единая точка регистрации и поиска адаптеров.

Жизненный цикл:
    1. bot.py (или другой инициализатор) создаёт SourceRegistry.
    2. Каждый адаптер регистрируется через registry.register(adapter).
    3. EvidenceEngine и orchestrator используют registry для поиска
       нужного адаптера по capability.

Хранение в памяти:
    In-memory dict по source_id. При регистрации DataSource
    дополнительно персистируется в IntelligenceStore.

Персистентность:
    DataSource записывается в data_sources при каждом старте —
    это нормально (upsert). Исторические данные (KnowledgeItem,
    Evidence) не зависят от этой записи: они ссылаются по source_id.
"""

from __future__ import annotations

import logging

from backend.intelligence.interfaces import IIntelligenceStore
from backend.intelligence.sources.base import DataSourceAdapter

log = logging.getLogger("selleros.intelligence.registry")


class SourceRegistry:
    """
    Реестр адаптеров внешних источников.

    store — IIntelligenceStore для персистирования DataSource записей.
            Если None — работает только в памяти (удобно в тестах).
    """

    def __init__(self, store: IIntelligenceStore | None = None) -> None:
        self._adapters: dict[str, DataSourceAdapter] = {}
        self._store = store

    async def register(self, adapter: DataSourceAdapter) -> None:
        """
        Зарегистрировать адаптер.

        Сохраняет DataSource в IntelligenceStore (upsert) и добавляет
        адаптер в in-memory реестр. Повторная регистрация обновляет запись.
        """
        sid = adapter.source_id
        self._adapters[sid] = adapter

        data_source = adapter.to_data_source()

        if self._store is not None:
            try:
                await self._store.save_source(data_source)
            except Exception as exc:
                log.warning(
                    "Не удалось сохранить DataSource %r в store: %s", sid, exc
                )

        log.info(
            "Источник зарегистрирован: %s | capabilities=%s",
            sid,
            adapter.capabilities,
        )

    def get(self, source_id: str) -> DataSourceAdapter | None:
        """Получить адаптер по id. None — если не зарегистрирован."""
        return self._adapters.get(source_id)

    def list_by_capability(self, capability: str) -> list[DataSourceAdapter]:
        """
        Список всех адаптеров, поддерживающих заданную capability.

        Используется для поиска «кто может ответить на этот вопрос»:
            registry.list_by_capability("search_demand")
            → [WordstatAdapter, ...]
        """
        return [
            adapter
            for adapter in self._adapters.values()
            if capability in adapter.capabilities
        ]

    def list_all(self) -> list[DataSourceAdapter]:
        """Все зарегистрированные адаптеры."""
        return list(self._adapters.values())

    async def check_availability(self) -> dict[str, bool]:
        """
        Проверить доступность всех зарегистрированных источников.

        Возвращает dict {source_id: is_available}.
        Ошибки отдельных источников не прерывают проверку остальных.
        """
        results: dict[str, bool] = {}

        for source_id, adapter in self._adapters.items():
            try:
                available = await adapter.is_available()
            except Exception as exc:
                log.warning("Ошибка проверки %r: %s", source_id, exc)
                available = False

            results[source_id] = available

        return results

    def __len__(self) -> int:
        return len(self._adapters)

    def __repr__(self) -> str:
        ids = list(self._adapters.keys())
        return f"SourceRegistry(sources={ids})"
