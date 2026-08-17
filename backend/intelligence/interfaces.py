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

from backend.intelligence.learning import ActionOutcome, LearningSignal
from backend.intelligence.outcomes import RecommendationOutcome
from backend.intelligence.models import (
    DataSource,
    Evidence,
    EvidenceType,
    KnowledgeItem,
    MarketEvent,
    ReviewAssessment,
    ReviewIssue,
    ReviewSignal,
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

    # ──────────────────────────────── api call tracking ─────────────────── #

    @abstractmethod
    async def record_api_call(
        self,
        call_id: str,
        source_id: str,
        query: str | None,
        category: str | None,
        region: str | None,
        called_at: float,
    ) -> None:
        """Записать факт реального HTTP-вызова к внешнему API."""

    @abstractmethod
    async def count_api_calls(
        self,
        source_id: str,
        since_ts: float,
    ) -> int:
        """Подсчитать реальные HTTP-вызовы для source_id после since_ts."""

    @abstractmethod
    async def search_items_by_query(
        self,
        query: str,
        source_id: str | None = None,
        since_ts: float | None = None,
        limit: int = 50,
    ) -> list[KnowledgeItem]:
        """
        Поиск KnowledgeItem по значению metadata.query.
        Используется YandexCostGuard для проверки кэша.
        """

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

    # ──────────────────────────────── learning loop ─────────────────────── #

    @abstractmethod
    async def save_action_outcome(self, outcome: ActionOutcome) -> None:
        """Сохранить / обновить ActionOutcome."""

    @abstractmethod
    async def get_action_outcome(self, outcome_id: str) -> ActionOutcome | None:
        """Получить ActionOutcome по id. None если не найден."""

    @abstractmethod
    async def search_action_outcomes(
        self,
        *,
        category: str | None = None,
        action: str | None = None,
        user_hash: str | None = None,
        since_ts: float | None = None,
        limit: int = 100,
    ) -> list[ActionOutcome]:
        """Поиск outcomes по фильтрам. Сортировка по period_end DESC."""

    @abstractmethod
    async def save_learning_signal(self, signal: LearningSignal) -> None:
        """Сохранить LearningSignal."""

    @abstractmethod
    async def search_learning_signals(
        self,
        *,
        outcome_id: str | None = None,
        signal_type: str | None = None,
        limit: int = 100,
    ) -> list[LearningSignal]:
        """Поиск learning signals по фильтрам."""

    @abstractmethod
    async def find_learning_signal_by_source_outcome(
        self,
        source_outcome_id: str,
    ) -> LearningSignal | None:
        """Найти LearningSignal по metadata.source_outcome_id (idempotency)."""

    # ──────────────────────────────── recommendation outcomes ───────────── #

    @abstractmethod
    async def save_recommendation_outcome(
        self, outcome: RecommendationOutcome,
    ) -> None:
        """Сохранить / обновить RecommendationOutcome."""

    @abstractmethod
    async def get_recommendation_outcome(
        self, outcome_id: str,
    ) -> RecommendationOutcome | None:
        """Получить RecommendationOutcome по id."""

    @abstractmethod
    async def search_recommendation_outcomes(
        self,
        *,
        category: str | None = None,
        article: str | None = None,
        recommendation_type: str | None = None,
        outcome_direction: str | None = None,
        days: int | None = 90,
        limit: int = 100,
    ) -> list[RecommendationOutcome]:
        """Поиск recommendation outcomes. Сортировка по recommended_at DESC."""

    # ──────────────────────────────── review intelligence ───────────────── #

    @abstractmethod
    async def save_review_signal(self, signal: ReviewSignal) -> None:
        """Сохранить / обновить ReviewSignal."""

    @abstractmethod
    async def save_review_issue(self, issue: ReviewIssue) -> None:
        """Сохранить / обновить ReviewIssue."""

    @abstractmethod
    async def search_review_signals(
        self,
        *,
        user_hash: str | None = None,
        category: str | None = None,
        article: str | None = None,
        signal_type: str | None = None,
        since_ts: float | None = None,
        limit: int = 100,
    ) -> list[ReviewSignal]:
        """Поиск review signals. Сортировка по created_at DESC."""

    @abstractmethod
    async def search_review_issues(
        self,
        *,
        user_hash: str | None = None,
        category: str | None = None,
        article: str | None = None,
        signal_type: str | None = None,
        sentiment: str | None = None,
        min_count: int = 1,
        limit: int = 50,
    ) -> list[ReviewIssue]:
        """Поиск review issues. Сортировка по count DESC."""

    @abstractmethod
    async def get_review_assessment(
        self,
        *,
        user_hash: str,
        category: str | None = None,
        article: str | None = None,
        days: int = 30,
    ) -> ReviewAssessment | None:
        """Свежий ReviewAssessment из сохранённых signals/issues."""
