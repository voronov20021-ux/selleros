"""
event_sources/base.py — контракт адаптера источника рыночных событий.

EventSourceAdapter отличается от DataSourceAdapter тем, что возвращает
уже классифицированные MarketEvent, а не сырые KnowledgeItem.
Классификация — ответственность самого адаптера.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from backend.intelligence.models import DataSource, MarketEvent


class EventSourceAdapter(ABC):
    """
    Абстрактный адаптер источника рыночных событий.

    Возвращает list[MarketEvent] — уже классифицированные события.
    Адаптер НЕ знает про store; MarketEventEngine сохраняет события сам.
    """

    @property
    @abstractmethod
    def source_id(self) -> str:
        """Уникальный строковый идентификатор источника."""

    @property
    @abstractmethod
    def capabilities(self) -> list[str]:
        """
        Список ключей возможностей источника.

        Стандартные ключи для событийных адаптеров:
            "market_news"       — новости рынка
            "platform_news"     — новости маркетплейса (WB, Ozon…)
            "regulation_news"   — изменения законодательства/правил
            "competitor_news"   — действия конкурентов
            "category_news"     — события в конкретной категории
            "sale_events"       — распродажи и акции
            "holiday_events"    — праздничные события
        """

    @abstractmethod
    async def is_available(self) -> bool:
        """
        Проверить, что источник доступен.
        Не делает реальный запрос данных.
        При ошибке — возвращает False, не бросает исключение.
        """

    @abstractmethod
    async def fetch(
        self,
        *,
        query: str,
        category: str | None = None,
        region: str = "RU",
        limit: int = 10,
    ) -> list[MarketEvent]:
        """
        Получить и классифицировать рыночные события.

        Правила строгости:
        - НЕ возвращать события, которые нельзя уверенно классифицировать.
        - НЕ выдумывать содержание, только то, что есть в источнике.
        - Если событие неоднозначно — пропустить (вернуть без него).
        - При недоступности источника — вернуть [].
        """

    def to_data_source(self) -> DataSource:
        """Создать DataSource-запись для реестра."""
        from backend.intelligence.models import SourceType
        return DataSource(
            id=self.source_id,
            name=self.source_id.replace("_", " ").title(),
            source_type=SourceType.PUBLIC_API,
            authority=0.55,
            freshness_hours=6,
            capabilities=list(self.capabilities),
        )
