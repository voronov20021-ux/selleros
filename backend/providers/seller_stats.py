"""
SellerStatsProvider — интерфейс СТАТИСТИКИ ПРОДАВЦА (этап 3, пункт 8).

Это заготовка под официальный Seller API.
Реализации пока НЕТ и сейчас она не пишется — только контракт.

Когда появится ключ Seller API Wildberries, реализуем
WBSellerStatsProvider — и разделы «Что сделать сегодня»
и «Отчёты» начнут показывать живые цифры:

    остатки, заказы, выручку, CTR, CR, расходы,
    остатки по складам.

Ни один существующий файл при этом менять не придётся.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


# --------------------------------------------------------------------------- #
# Модели данных Seller API
# --------------------------------------------------------------------------- #

@dataclass
class SellerStock:
    """Остаток по одному товару."""
    article: int
    total_qty: int
    # {"Коледино": 120, "Казань": 40, ...}
    by_warehouse: dict[str, int] = field(default_factory=dict)


@dataclass
class SellerOrders:
    """Заказы за период."""
    period: str                 # "day" | "week" | "month" | "year"
    orders_count: int = 0
    items_count: int = 0
    revenue: float = 0.0        # выручка, руб.
    cancelled: int = 0


@dataclass
class SellerFunnel:
    """Воронка карточки за период."""
    article: int
    views: int = 0
    clicks: int = 0
    add_to_cart: int = 0
    orders: int = 0
    ctr: float = 0.0            # клики / показы
    cr: float = 0.0             # заказы / клики


@dataclass
class SellerExpenses:
    """Расходы за период."""
    period: str
    advertising: float = 0.0
    logistics: float = 0.0
    storage: float = 0.0
    commission: float = 0.0

    @property
    def total(self) -> float:
        return self.advertising + self.logistics + self.storage + self.commission


# --------------------------------------------------------------------------- #
# Интерфейс
# --------------------------------------------------------------------------- #

class SellerStatsProvider(ABC):
    """Контракт источника статистики продавца."""

    name: str = "base_stats"
    marketplace: str = "wildberries"

    async def is_available(self) -> bool:
        return True

    @abstractmethod
    async def get_stocks(self) -> list[SellerStock]:
        """Остатки по всем товарам (включая разбивку по складам)."""

    @abstractmethod
    async def get_orders(self, period: str) -> SellerOrders:
        """Заказы и выручка за период."""

    @abstractmethod
    async def get_funnel(self, article: int, period: str) -> SellerFunnel:
        """Воронка карточки: показы, клики, CTR, CR."""

    @abstractmethod
    async def get_expenses(self, period: str) -> SellerExpenses:
        """Расходы: реклама, логистика, хранение, комиссия."""


# --------------------------------------------------------------------------- #
# Заглушка под официальный Seller API Wildberries
# --------------------------------------------------------------------------- #

class WBSellerStatsProvider(SellerStatsProvider):
    """
    TODO: реализовать после получения ключа Seller API.

    Подключение (одна строка в bot.py):
        dp["seller_stats"] = WBSellerStatsProvider(api_key=...)
    """

    name = "wb_seller_stats"
    marketplace = "wildberries"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key

    async def is_available(self) -> bool:
        # Ключа нет — источник выключен.
        return bool(self.api_key)

    async def get_stocks(self) -> list[SellerStock]:
        raise NotImplementedError("Seller API ещё не подключён")

    async def get_orders(self, period: str) -> SellerOrders:
        raise NotImplementedError("Seller API ещё не подключён")

    async def get_funnel(self, article: int, period: str) -> SellerFunnel:
        raise NotImplementedError("Seller API ещё не подключён")

    async def get_expenses(self, period: str) -> SellerExpenses:
        raise NotImplementedError("Seller API ещё не подключён")
