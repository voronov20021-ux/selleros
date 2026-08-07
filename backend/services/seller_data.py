"""
seller_data.py — данные о товаре, которые предоставил ПРОДАВЕЦ (не карточка WB).

Разделение принципиально:
    WBProduct  (backend/wb/cdn_provider.py) — что получилось спарсить с
               карточки Wildberries: название, описание, характеристики,
               фото. Цена/рейтинг/отзывы там тоже есть, но могут быть None,
               если публичный WB API их не отдал.

    SellerData (этот файл) — цена/рейтинг/отзывы/продажи/заказы, которые
               ЛИБО ввёл продавец вручную, ЛИБО (в будущем) отдал
               официальный Seller API. У каждого поля есть свой source,
               поэтому в отчёте всегда видно, откуда взялась цифра.

Не дублирует WBProduct и не заменяет его — это отдельная сущность
«данные продавца», как и требует сценарий проекта.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

#: Допустимые источники значения одного поля.
SOURCE_USER = "user"
SOURCE_API = "api"


@dataclass
class SellerData:
    """Снимок данных продавца по одному товару."""

    price: Optional[float] = None
    rating: Optional[float] = None
    feedbacks: Optional[int] = None

    sales: Optional[int] = None
    orders: Optional[int] = None
    period: Optional[str] = None

    price_source: Optional[str] = None
    rating_source: Optional[str] = None
    feedbacks_source: Optional[str] = None
    sales_source: Optional[str] = None
    orders_source: Optional[str] = None
    period_source: Optional[str] = None

    updated_at: Optional[datetime] = None

    def has_minimum(self) -> bool:
        """Есть ли хотя бы обязательный минимум: цена, рейтинг, отзывы."""
        return self.price is not None and self.rating is not None and self.feedbacks is not None
