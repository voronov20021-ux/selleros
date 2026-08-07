"""
WBSellerAPIProvider — ЗАГОТОВКА под официальный Seller API Wildberries.

Пока НЕ реализован (по плану проекта).

Когда получим API-ключ, здесь появится реализация,
и подключение займёт одну строку в bot.py:

    product_service.register(WBSellerAPIProvider(api_key=...), priority=0)

Больше нигде в проекте ничего менять не придётся —
в этом весь смысл интерфейса ProductProvider.
"""

from backend.providers.base import ProductProvider
from backend.wb.cdn_provider import WBProduct


class WBSellerAPIProvider(ProductProvider):

    name = "wb_seller_api"
    marketplace = "wildberries"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key

    async def is_available(self) -> bool:
        # Пока ключа нет — источник считается выключенным,
        # и ProductService его просто пропускает.
        return False

    async def get_product(self, article: int) -> WBProduct | None:
        # TODO: реализовать после получения ключа Seller API.
        return None
