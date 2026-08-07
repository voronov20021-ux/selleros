"""
seller_api.py — заготовка под официальный Seller API Wildberries.

Реализации пока нет — только каркас. Когда появится ключ,
подключение сведётся к одной строке в bot.py:

    engine.register(SellerAPISource(api_key=...), priority=0)

Пока ключа нет, is_available() возвращает False, и WBEngine
даже не пытается его дёргать — сразу переходит к следующему
источнику по приоритету.
"""

from __future__ import annotations

from backend.wb.cdn_provider import WBProduct
from backend.wb_engine.source import DataSource


class SellerAPISource(DataSource):

    name = "seller_api"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key

    async def is_available(self) -> bool:
        return bool(self.api_key)

    async def fetch(self, article: int) -> WBProduct | None:
        # TODO: реализовать после получения ключа официального Seller API.
        # Это единственное место, которое придётся дописать —
        # WBEngine, кэш, cooldown и остальные источники трогать не нужно.
        raise NotImplementedError("Seller API ещё не подключён")
