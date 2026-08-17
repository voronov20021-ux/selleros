"""
history_fallback.py — источник №4, последний резерв: без сети вообще.

Если Seller API, CDN и поиск — все трое молчат (например, WB сегодня
особенно агрессивно банит), это НЕ повод отвечать продавцу пустотой.
Если ARGUS уже видел этот товар — хоть у этого продавца, хоть у
другого — честнее отдать последний известный снимок с пометкой
«данные могут быть устаревшими» (product.source == "history"),
чем говорить «карточка недоступна».

Этот источник всегда is_available() — он не ходит в сеть, поэтому
падать ему особо не от чего. Именно поэтому у него самый низкий
приоритет: это подстраховка, а не полноценная замена живым данным.
"""

from __future__ import annotations

from backend.memory import MemoryStore
from backend.wb.cdn_provider import WBProduct
from backend.wb_engine.source import DataSource


class HistoryFallbackSource(DataSource):

    name = "history"

    def __init__(self, memory_store: MemoryStore):
        self.store = memory_store

    async def fetch(self, article: int) -> WBProduct | None:
        record = await self.store.get_last_known_snapshot(article, marketplace="wildberries")

        if record is None:
            return None

        product = WBProduct(
            article=record.article,
            title=record.title,
            price=record.price,
            rating=record.rating,
            photo_count=record.photos,
            imt_id=getattr(record, "imt_id", None),
            root_id=getattr(record, "root_id", None),
        )
        if product.imt_id is None and product.root_id is not None:
            product.imt_id = product.root_id
        if product.root_id is None and product.imt_id is not None:
            product.root_id = product.imt_id
        product.source = "history"
        product.scanned_at = record.last_seen

        return product
