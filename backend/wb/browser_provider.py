from backend.wb.search_provider import WBSearchProvider
from backend.wb.card_provider import get_product
from backend.wb.browser_manager import BrowserManager


class WBProvider:

    def __init__(self):

        self.search_provider = WBSearchProvider()
        self.browser = BrowserManager()

    async def search(self, query: str):

        return await self.search_provider.search(query)

    async def get_product(self, article: int):

        # 1. Сначала пробуем быстрый API
        product = await get_product(article)

        if product is not None:
            return product

        print("Card API не нашёл товар. Пробуем BrowserManager...")

        # 2. Если API не помог — используем браузер
        try:

            product = await self.browser.get_product(article)

            if product is not None:
                return product

        except Exception as e:

            print("BrowserManager ERROR:", e)

        # 3. Пока ProxyProvider ещё не подключён
        return None