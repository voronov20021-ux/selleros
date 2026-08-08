"""
sources/base.py — контракт адаптера внешнего источника данных.

DataSourceAdapter определяет единый интерфейс для всех источников:
Wordstat, Official WB API, Custom scrapers, …

Чтобы добавить новый источник:
    1. Унаследоваться от DataSourceAdapter.
    2. Установить source_id и capabilities.
    3. Реализовать is_available() и fetch().
    4. Зарегистрировать в SourceRegistry.

Адаптер НЕ знает про хранилище — он только возвращает список
KnowledgeItem. Сохранение и обработку делает вызывающий код
(SourceRegistry или отдельный orchestrator).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from backend.intelligence.models import DataSource, KnowledgeItem


class DataSourceAdapter(ABC):
    """
    Абстрактный адаптер внешнего источника данных.

    Каждый конкретный адаптер:
        - имеет уникальный source_id (строка-ключ);
        - объявляет список capabilities (что умеет возвращать);
        - проверяет доступность (is_available);
        - возвращает сырые KnowledgeItem из fetch().

    fetch() НИКОГДА не возвращает выдуманные данные.
    Если данные недоступны — возвращает пустой список или бросает исключение.
    """

    @property
    @abstractmethod
    def source_id(self) -> str:
        """
        Уникальный строковый идентификатор источника.
        Совпадает с DataSource.id в реестре.
        Пример: "yandex_wordstat", "wb_official_api", "wb_seller_api"
        """

    @property
    @abstractmethod
    def capabilities(self) -> list[str]:
        """
        Список строк-идентификаторов того, что источник умеет возвращать.

        Стандартные ключи (соглашение, не enum — для расширяемости):
            "search_demand"         — объём поискового спроса (показы/клики)
            "query_dynamics"        — динамика запроса по периодам
            "top_related_queries"   — топ связанных запросов
            "regional_demand"       — спрос по регионам
            "category_trends"       — тренды по категориям
            "market_events"         — внешние события (акции, изменения правил)
            "seasonality"           — сезонные индексы
            "price_analytics"       — ценовая аналитика

        Используется SourceRegistry.list_by_capability() для поиска
        источника, умеющего ответить на конкретный тип запроса.
        """

    @abstractmethod
    async def is_available(self) -> bool:
        """
        Проверить, что источник доступен прямо сейчас:
        API-ключ есть, сервис отвечает, лимиты не исчерпаны.

        НЕ делает реальный запрос данных — только проверку готовности.
        Не должен бросать исключение: при любой ошибке возвращает False.
        """

    @abstractmethod
    async def fetch(self, **kwargs) -> list[KnowledgeItem]:
        """
        Получить данные из источника.

        Параметры специфичны для каждого адаптера — передаются как kwargs.
        Примеры:
            WordstatAdapter.fetch(query="мужские часы", region_id=225)
            MarketEventsAdapter.fetch(category="Часы", days_back=30)

        Возвращает список KnowledgeItem с полностью заполненными полями:
            id, source_id, collected_at, item_type, content, metadata, …

        При отсутствии данных возвращает [].
        При недоступности API — бросает SourceUnavailableError.
        """

    def to_data_source(self) -> DataSource:
        """
        Создать DataSource-запись для реестра.

        Конкретные адаптеры переопределяют это для задания правильных
        authority, freshness_hours и metadata.
        """
        from backend.intelligence.models import SourceType
        return DataSource(
            id=self.source_id,
            name=self.source_id.replace("_", " ").title(),
            source_type=SourceType.PUBLIC_API,
            authority=0.5,
            freshness_hours=24,
            capabilities=list(self.capabilities),
        )


class SourceUnavailableError(Exception):
    """
    Источник временно недоступен (нет ключа, ошибка сети, лимит исчерпан).

    Не выдуманные данные — просто сигнал, что fetch() не может выполниться.
    Вызывающий код решает: retry, fallback, или пропустить.
    """

    def __init__(self, source_id: str, reason: str) -> None:
        self.source_id = source_id
        self.reason = reason
        super().__init__(f"[{source_id}] {reason}")


class SourceNotImplementedError(NotImplementedError):
    """
    Адаптер объявлен, но реализация fetch() ещё не написана.

    Используется в стаб-адаптерах (например, WordstatAdapter на этапе,
    когда API-ключ ещё не получен) — чтобы явно сигнализировать о том,
    что это заготовка, а не рабочий код.
    """

    def __init__(self, source_id: str, message: str = "") -> None:
        self.source_id = source_id
        text = message or f"Адаптер {source_id!r}: реализация fetch() не завершена."
        super().__init__(text)
