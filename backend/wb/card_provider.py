import aiohttp

from backend.core.errors import WBRateLimitError


async def get_product(article: int):
    url = "https://card.wb.ru/cards/v4/detail"

    params = {
        "appType": 1,
        "curr": "rub",
        "dest": -1257786,
        "nm": article,
    }

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(url, params=params) as response:

            if response.status == 429:
                raise WBRateLimitError("Card API: WB заблокировал частые запросы")

            if response.status != 200:
                return None

            data = await response.json()

            products = data.get("data", {}).get("products", [])

            if not products:
                return None

            return products[0]