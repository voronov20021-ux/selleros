import asyncio

from backend.core.gateway import WBGateway


class BrowserPool:

    def __init__(self):

        self.gateway = WBGateway()

    async def start(self):

        print("Gateway started")

    async def stop(self):

        print("Gateway stopped")

    async def search(self, query: str):

        return await self.gateway.search(query)

    async def get_product(self, article: int):

        return await self.gateway.get_product(article)