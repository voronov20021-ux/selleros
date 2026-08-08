"""
interfaces.py — контракт хранилища Intelligence Layer.

IIntelligenceStore описывает ВСЕ операции с данными.
Бизнес-логика (EvidenceEngine, SourceRegistry и будущий ReasoningLayer)
зависит только от этого интерфейса — не от конкретной БД.

Смена SQLite → PostgreSQL = заменить один класс-реализацию,
не трогая ни одного вызывающего файла.

Текущая реализация — IntelligenceStore в store.py (aiosqlite).
Будущая — PgIntelligenceStore (asyncpg), реализует тот же интерфейс.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from backend.intelligence.models import (
    DataSource,
    Evidence,
    EvidenceType,
    KnowledgeItem,
    MarketEvent,
    SeasonalityRecord,
    SellerObservation,
    TrendRecord,
)


class IIntelligenceStore(ABC):
    """Абстрактный репозиторий Intelligence Layer."""

    # ──────────────────────────────── lifecycle ─────────────────────────── #

    @abstractmethod
    async def connect(self) -> None:
        """Открыть соединение с БД / создать схему при первом запуске."""

    @abstractmethod
    async def close(self) -> None:
        """Закрыть соединение."""

    # ──────────────────────────────── sources ───────────────────────────── #

    @abstractmethod
    async def save_source(self, source: DataSource) -> None:
        """
        Сохранить/обновить источник данных.
        Если источник с таким id уже существует — обновить его поля.
        """

    @abstractmethod
    async def get_source(self, source_id: str) -> DataSource | None:
        """Получить источник по id. None — если не зарегистрирован."""

    @abstractmethod
    async def list_sources(
        self,
        *,
        active_only: bool = True,
    ) -> list[DataSource]:
        """Список всех зарегистрированных источников."""

    # ──────────────────────────────── knowledge items ───────────────────── #

    @abstractmethod
    async def save_item(self, item: KnowledgeItem) -> None:
        """Сохранить сырую запись из источника."""

    @abstractmethod
    async def get_item(self, item_id: str) -> KnowledgeItem | None:
        """Получить запись по id."""

    @abstractmethod
    async def search_items(
        self,
        *,
        source_id: str | None = None,
        category: str | None = None,
        region: str | None = None,
        min_confidence: float = 0.0,
        limit: int = 50,
    ) -> list[KnowledgeItem]:
        """
        Поиск сырых записей по фильтрам.
        Результат сортируется по collected_at DESC (свежие — первые).
        """

    # ──────────────────────────────── evidence ──────────────────────────── #

    @abstractmethod
    async def save_evidence(self, evidence: Evidence) -> None:
        """Сохранить обработанную единицу знания."""

    @abstractmethod
    async def get_evidence(self, evidence_id: str) -> Evidence | None:
        """Получить evidence по id."""

    @abstractmethod
    async def retrieve_evidence(
        self,
        *,
        evidence_type: EvidenceType | None = None,
        category: str | None = None,
        min_confidence: float = 0.3,
        limit: int = 20,
    ) -> list[Evidence]:
        """
        Выборка evidence для Argus reasoning.

        Возвращает записи, отсортированные по confidence DESC.
        min_confidence отфильтровывает слишком слабые выводы.
        """

    # ──────────────────────────────── observations ──────────────────────── #

    @abstractmethod
    async def save_observation(self, obs: SellerObservation) -> None:
        """Сохранить обезличенное наблюдение продавца."""

    @abstractmethod
    async def list_observations(
        self,
        *,
        category: str | None = None,
        change_type: str | None = None,
        limit: int = 100,
    ) -> list[SellerObservation]:
        """Список наблюдений по фильтрам (для агрегации и обучения)."""

    # ──────────────────────────────── seasonality ───────────────────────── #

    @abstractmethod
    async def save_seasonality(self, record: SeasonalityRecord) -> None:
        """Сохранить/обновить запись сезонности."""

    @abstractmethod
    async def get_seasonality(
        self,
        category: str,
        region: str,
        month: int,
    ) -> list[SeasonalityRecord]:
        """
        Все записи сезонности для данной категории/региона/месяца.
        Может быть несколько — из разных источников / годов.
        Caller агрегирует их сам (например, по среднему или последнему).
        """

    # ──────────────────────────────── trends ────────────────────────────── #

    @abstractmethod
    async def save_trend(self, record: TrendRecord) -> None:
        """Сохранить запись тренда."""

    @abstractmethod
    async def list_trends(
        self,
        *,
        category: str | None = None,
        query: str | None = None,
        region: str | None = None,
        limit: int = 50,
    ) -> list[TrendRecord]:
        """Список трендов по фильтрам, сортировка по period_start DESC."""

    # ──────────────────────────────── market events ─────────────────────── #

    @abstractmethod
    async def save_market_event(self, event: MarketEvent) -> None:
        """Сохранить рыночное событие."""

    @abstractmethod
    async def list_market_events(
        self,
        *,
        category: str | None = None,
        event_type: str | None = None,
        after_ts: float | None = None,
        limit: int = 50,
    ) -> list[MarketEvent]:
        """
        Список событий, опционально фильтрованных по категории, типу,
        или дате начала (after_ts — unix time).
        Сортировка по event_date DESC.
        """
