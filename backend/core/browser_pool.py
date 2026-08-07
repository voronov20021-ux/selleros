import asyncio

from backend.core.gateway import WBGateway
from backend.core.queue import WBWorker
from backend.core.rate_limiter import RateLimiter


class BrowserPool:

    def __init__(self, workers: int = 3):

        self.workers = workers

        self.providers = []

        self.queue = asyncio.Queue()

        self.started = False

        # ОДИН турникет и ОДИН WBWorker на весь пул —
        # не по одному на каждого из 3 воркеров.
        self.limiter = RateLimiter()
        self.wb_worker = WBWorker(limiter=self.limiter)

    async def start(self):

        if self.started:
            return

        self.started = True

        # Включаем турникет один раз.
        await self.wb_worker.start()

        for _ in range(self.workers):

            provider = WBGateway(worker=self.wb_worker)

            self.providers.append(provider)

            await self.queue.put(provider)

        print(f"BrowserPool started ({self.workers} browsers)")

    async def stop(self):

        while not self.queue.empty():
            await self.queue.get()

        self.providers.clear()

        self.started = False

        # Выключаем турникет.
        await self.wb_worker.stop()

        print("BrowserPool stopped")

    async def _take(self):

        provider = await self.queue.get()

        return provider

    async def _release(self, provider):

        await self.queue.put(provider)

    async def search(self, query: str):

        provider = await self._take()

        try:

            return await provider.search(query)

        finally:

            await self._release(provider)

    async def get_product(self, article: int):

        provider = await self._take()

        try:

            return await provider.get_product(article)

        finally:

            await self._release(provider)