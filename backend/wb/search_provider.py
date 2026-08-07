import aiohttp
import asyncio
import json

from backend.core.errors import WBRateLimitError


class WBSearchProvider:

    def __init__(self):
        self.url = (
            "https://search.wb.ru/exactmatch/ru/common/v18/search"
        )


    async def search(self, query: str):

        params = {
            "appType": "1",
            "curr": "rub",
            "dest": "-1257786",
            "lang": "ru",
            "page": "1",
            "query": query,
            "resultset": "catalog",
            "sort": "popular",
            "spp": "30",
        }


        headers = {
            "User-Agent": (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/120 Safari/537.36"
            ),
            "Accept": "*/*",
            "Referer": "https://www.wildberries.ru/",
        }


        timeout = aiohttp.ClientTimeout(
            total=20
        )


        async with aiohttp.ClientSession(
            headers=headers,
            timeout=timeout
        ) as session:


            async with session.get(
                self.url,
                params=params
            ) as response:


                print(
                    "WB STATUS:",
                    response.status
                )


                text = await response.text()


                print(
                    "Первые 200 символов:",
                    text[:200]
                )


                # WB ограничил запросы
                if response.status == 429:

                    raise WBRateLimitError(
                        "WB заблокировал частые запросы"
                    )


                if response.status != 200:

                    raise Exception(
                        f"WB ERROR {response.status}"
                    )


                data = json.loads(
                    text
                )


                products = data.get(
                    "products",
                    []
                )


                print(
                    "Найдено товаров:",
                    len(products)
                )


                return products



async def test():

    provider = WBSearchProvider()


    products = await provider.search(
        "Redmi Note 15"
    )


    for product in products[:5]:

        print(
            product.get("name"),
            "|",
            product.get("brand"),
            "|",
            product.get("id")
        )



if __name__ == "__main__":

    asyncio.run(test())