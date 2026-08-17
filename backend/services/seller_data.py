"""
seller_data.py — данные о товаре, которые предоставил ПРОДАВЕЦ (не карточка WB).

Разделение принципиально:
    WBProduct  — CARD DATA: title/brand/photos/... + public price/rating/feedbacks
                 (в т.ч. verified browser.detail → PUBLIC_BROWSER).

    SellerData — SELLER / PRIVATE analytics:
                 опционально seller_price/rating/feedbacks (только если ввёл продавец
                 или API), плюс CTR/CVR/sales/orders/returns/ad_spend/cost/...

Правила:
    public_price ≠ seller_price.
    Не создавать seller_price автоматически из публичной цены.
    Private metrics всегда optional; отсутствующее = «нет данных».
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

#: Допустимые источники значения одного поля.
SOURCE_USER = "user"
SOURCE_API = "api"

#: Приватные метрики, которые public browser не отдаёт.
PRIVATE_METRIC_FIELDS = (
    "ctr",
    "cvr",
    "impressions",
    "views",
    "sales",
    "orders",
    "returns",
    "ad_spend",
    "cost",
    "commission",
    "logistics",
    "storage",
)


@dataclass
class SellerData:
    """Снимок данных продавца по одному товару (не карточка WB)."""

    # Gap-fill для коммерции карточки — только при явном вводе/API.
    # Не заполнять из public_price автоматически.
    price: Optional[float] = None
    rating: Optional[float] = None
    feedbacks: Optional[int] = None

    sales: Optional[int] = None
    orders: Optional[int] = None
    period: Optional[str] = None

    # Private metrics (optional)
    ctr: Optional[float] = None
    cvr: Optional[float] = None
    impressions: Optional[int] = None  # показы
    views: Optional[int] = None        # просмотры
    returns: Optional[int] = None
    ad_spend: Optional[float] = None
    cost: Optional[float] = None  # себестоимость
    commission: Optional[float] = None
    logistics: Optional[float] = None
    storage: Optional[float] = None

    price_source: Optional[str] = None
    rating_source: Optional[str] = None
    feedbacks_source: Optional[str] = None
    sales_source: Optional[str] = None
    orders_source: Optional[str] = None
    period_source: Optional[str] = None
    ctr_source: Optional[str] = None
    cvr_source: Optional[str] = None
    impressions_source: Optional[str] = None
    views_source: Optional[str] = None
    returns_source: Optional[str] = None
    ad_spend_source: Optional[str] = None
    cost_source: Optional[str] = None
    commission_source: Optional[str] = None
    logistics_source: Optional[str] = None
    storage_source: Optional[str] = None

    updated_at: Optional[datetime] = None
    #: True = продавец подтвердил актуальность в текущей сессии.
    confirmed_current: bool = False

    def has_minimum(self) -> bool:
        """Обязательный минимум gap-fill: цена, рейтинг, отзывы (если карточка пуста)."""
        return self.price is not None and self.rating is not None and self.feedbacks is not None

    def has_any_private_metrics(self) -> bool:
        return any(getattr(self, name, None) is not None for name in PRIVATE_METRIC_FIELDS)

    def has_any_seller_value(self) -> bool:
        return (
            self.price is not None
            or self.rating is not None
            or self.feedbacks is not None
            or self.has_any_private_metrics()
            or bool(self.period)
        )
