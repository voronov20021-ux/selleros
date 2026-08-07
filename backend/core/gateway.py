from backend.wb.search_provider import WBSearchProvider
from backend.wb.card_provider import get_product
from backend.wb.proxy_provider import ProxyProvider


class WBGateway:

    def __init__(self, worker):

        # Общий турникет на всех — передаём его снаружи,
        # а не создаём заново внутри каждого Gateway.
        self.worker = worker

        self.search_provider = WBSearchProvider()
        self.proxy_provider = ProxyProvider()

    async def search(self, query: str):

        # Поход в WB тоже идёт через турникет.
        return await self.worker.submit(
            lambda: self.search_provider.search(query)
        )

    async def get_product(self, article: int):

        # 1. Пытаемся получить карточку напрямую — через турникет.
        product = await self.worker.submit(
            lambda: get_product(article)
        )

        if product is not None:
            return product

        print("Card API не помог. Пробуем ProxyProvider...")

        # 2. Если API не помог — идём через ProxyProvider, тоже через турникет.
        product = await self.worker.submit(
            lambda: self.proxy_provider.get_product(article)
        )

        if product is not None:
            return product

        # 3. Позже сюда подключим BrowserProvider.
        return None