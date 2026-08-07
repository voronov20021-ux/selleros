"""
source.py — контракт источника данных для WB Engine.

Правило то же, что мы уже применяли для ProductProvider
и ContextSource: чтобы подключить новый источник (BrightData,
Playwright Cluster, официальный API), нужен один класс с методом
fetch() и одна строка engine.register(source, priority=N).
Больше нигде ничего менять не нужно.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from backend.wb.cdn_provider import WBProduct


class DataSource(ABC):
    """Один способ получить карточку товара с Wildberries."""

    #: Имя для логов и для внутреннего учёта "остывания" источника.
    name: str = "base"

    #: Считается ли источник постоянно доступным (Seller API без ключа —
    #: нет; CDN и поиск — да). WBEngine пропускает недоступные источники,
    #: даже не пытаясь их дёрнуть.
    async def is_available(self) -> bool:
        return True

    @abstractmethod
    async def fetch(self, article: int) -> WBProduct | None:
        """
        Получить товар.

        Должен вернуть WBProduct, вернуть None (если источник в принципе
        не смог ничего сказать) или поднять одно из исключений
        backend.wb_engine.errors — SourceBlocked / SourceNotFound /
        SourceUnavailable. Любое другое исключение WBEngine тоже
        поймает и не даст ему сломать всю цепочку, но тогда потеряется
        точность («это блокировка или просто товара нет?»).
        """
        raise NotImplementedError
