import asyncio

from backend.core.errors import WBRateLimitError
from backend.core.rate_limiter import RateLimiter


class WBWorker:

    def __init__(self, limiter: RateLimiter | None = None):

        self.queue = asyncio.Queue()

        self.task = None

        self.running = False

        # Турникет. Если снаружи не дали свой — создаём новый.
        self.limiter = limiter or RateLimiter()

    async def start(self):

        if self.running:
            return

        self.running = True

        self.task = asyncio.create_task(
            self._worker()
        )

    async def stop(self):

        self.running = False

        await self.queue.put(None)

        if self.task:
            await self.task

    async def submit(
        self,
        func
    ):

        future = asyncio.Future()

        await self.queue.put(
            (
                func,
                future
            )
        )

        return await future

    async def _worker(self):

        while self.running:

            item = await self.queue.get()

            if item is None:
                break

            func, future = item

            attempts = 0

            while attempts < 3:

                # Ждём своей очереди у турникета ПЕРЕД каждой попыткой.
                await self.limiter.wait()

                try:

                    result = await func()

                    # Получилось — турникет может пропускать чуть быстрее.
                    await self.limiter.success()

                    if not future.done():
                        future.set_result(result)

                    break

                except WBRateLimitError:

                    attempts += 1

                    # WB попросил притормозить — турникет замедляется сам.
                    await self.limiter.blocked()

                except Exception as e:

                    if not future.done():
                        future.set_exception(e)

                    break

            else:

                if not future.done():
                    future.set_exception(
                        Exception(
                            "WB временно недоступен после повторов"
                        )
                    )

            self.queue.task_done()