import asyncio
import time


class RateLimiter:

    def __init__(self, min_delay: float = 10, max_delay: float = 120):

        self.delay = min_delay
        self.min_delay = min_delay
        self.max_delay = max_delay

        self.last_request = 0

        self.lock = asyncio.Lock()

    async def wait(self):

        async with self.lock:

            now = time.time()
            diff = now - self.last_request

            if diff < self.delay:

                sleep_time = self.delay - diff

                print(f"WB LIMITER: ждём {round(sleep_time,1)} сек")

                await asyncio.sleep(sleep_time)

            self.last_request = time.time()

    async def success(self):

        async with self.lock:

            self.delay = max(
                self.min_delay,
                self.delay * 0.9
            )

            print(
                f"WB LIMITER: успех, задержка {round(self.delay,1)} сек"
            )

    async def blocked(self):

        async with self.lock:

            self.delay = min(
                self.max_delay,
                self.delay * 2
            )

            print(
                f"WB LIMITER: 429, новая задержка {round(self.delay,1)} сек"
            )