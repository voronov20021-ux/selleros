from backend.wb.search_provider import WBSearchProvider


class ProxyProvider:

    def __init__(self):
        self.search_provider = WBSearchProvider()

    async def search(self, query: str):
        return await self.search_provider.search(query)

    async def get_product(self, article: int):

        products = await self.search_provider.search(str(article))

        if not products:
            return None

        return products[0]