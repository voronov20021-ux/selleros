import asyncio
import time


class TTLCache:


    def __init__(self):

        self.data = {}

        # тут храним запросы,
        # которые уже выполняются
        self.pending = {}



    def get(
        self,
        key: str
    ):

        item = self.data.get(key)


        if not item:
            return None


        value, expires = item


        if time.time() > expires:

            del self.data[key]

            return None


        return value




    def set(
        self,
        key: str,
        value,
        ttl: int = 3600
    ):

        self.data[key] = (
            value,
            time.time() + ttl
        )




    async def get_or_wait(
        self,
        key: str,
        callback,
        ttl: int = 3600
    ):

        # 1. Есть готовый результат
        cached = self.get(key)


        if cached is not None:

            print(
                "CACHE HIT:",
                key
            )

            return cached



        # 2. Такой запрос уже выполняется

        if key in self.pending:

            print(
                "WAIT EXISTING REQUEST:",
                key
            )

            return await self.pending[key]



        # 3. Создаём новый запрос

        loop = asyncio.get_running_loop()


        future = loop.create_future()


        self.pending[key] = future


        try:

            print(
                "NEW WB REQUEST:",
                key
            )


            result = await callback()


            self.set(
                key,
                result,
                ttl
            )


            future.set_result(
                result
            )


            return result



        except Exception as e:


            future.set_exception(
                e
            )


            raise



        finally:

             self.pending.pop(key, None)