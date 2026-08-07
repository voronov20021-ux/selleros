"""
base.py — контракт источника контекста.

Это задел под RAG (пункт 9 плана).

Идея: Seller AI не знает, откуда берутся знания о продавце.
Он просит ContextBuilder «собери всё, что относится к этому вопросу»,
и получает готовый текстовый блок.

Сегодня источники — память сессии и история анализов.
Завтра сюда встанут без единой правки остального кода:

    SellerAPIContextSource   — остатки, заказы, выручка, CTR, CR
    OrdersHistorySource      — история заказов
    KnowledgeBaseSource      — векторный поиск по базе знаний (настоящий RAG)
    CompetitorSource         — карточки конкурентов

Чтобы добавить источник, нужно:
    1. отнаследоваться от ContextSource;
    2. зарегистрировать его в ContextBuilder (одна строка в bot.py).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from backend.ai.intents import Intent


@dataclass
class ContextRequest:
    """Всё, что известно о запросе, — на входе у источника."""

    user_id: int
    text: str
    intent: Intent
    #: Произвольные данные от вызывающего кода (например, режим диалога).
    extra: dict = field(default_factory=dict)


@dataclass
class ContextBlock:
    """Кусок контекста, который уйдёт в промпт."""

    #: Заголовок блока, например «ТОВАР В РАБОТЕ».
    title: str
    #: Текст блока.
    body: str
    #: Чем меньше число, тем выше блок в промпте.
    priority: int = 50

    def render(self) -> str:
        return f"{self.title}\n\n{self.body}".strip()

    def __len__(self) -> int:
        return len(self.body)


class ContextSource(ABC):
    """Источник знаний для Seller AI."""

    #: Имя для логов.
    name: str = "base"

    #: Для каких типов вопросов источник полезен.
    #: Пустой набор = полезен всегда.
    intents: frozenset[Intent] = frozenset()

    def relevant_for(self, request: ContextRequest) -> bool:
        if not self.intents:
            return True
        return request.intent in self.intents

    @abstractmethod
    async def fetch(self, request: ContextRequest) -> ContextBlock | None:
        """Вернуть блок контекста или None, если данных нет."""
        raise NotImplementedError
