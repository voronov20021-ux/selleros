"""
intelligence/sources — адаптеры внешних источников данных.

Публичное API:
    DataSourceAdapter       — абстрактный контракт адаптера
    SourceUnavailableError  — источник временно недоступен
    SourceNotImplementedError — адаптер ещё не реализован
    SourceRegistry          — реестр адаптеров
    WordstatAdapter         — Yandex Wordstat (интерфейс, fetch не реализован)
    YandexSearchAdapter     — Yandex Search API (рабочая реализация)
"""

from backend.intelligence.sources.base import (
    DataSourceAdapter,
    SourceNotImplementedError,
    SourceUnavailableError,
)
from backend.intelligence.sources.registry import SourceRegistry
from backend.intelligence.sources.wordstat import WordstatAdapter
from backend.intelligence.sources.yandex_search import YandexSearchAdapter

__all__ = [
    "DataSourceAdapter",
    "SourceUnavailableError",
    "SourceNotImplementedError",
    "SourceRegistry",
    "WordstatAdapter",
    "YandexSearchAdapter",
]
