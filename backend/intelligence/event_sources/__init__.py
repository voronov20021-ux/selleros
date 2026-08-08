"""event_sources — адаптеры источников рыночных событий."""

from backend.intelligence.event_sources.base import EventSourceAdapter
from backend.intelligence.event_sources.yandex_news import YandexNewsAdapter

__all__ = ["EventSourceAdapter", "YandexNewsAdapter"]
