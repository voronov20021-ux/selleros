import re
import aiohttp
from dataclasses import dataclass


class WildberriesParsingError(Exception):
    pass


@dataclass
class WildberriesProduct:
    nm_id: int
    name: str
    price: float | None
    rating: float | None
    feedbacks_count: int | None


class WildberriesPublicProvider:

    def extract_nm_id(self, url: str):
        if url.isdigit():
            return int(url)

        match = re.search(r"/catalog/(\d+)/", url)
        if match:
            return int(match.group(1))

        return None

    async def get_product(self, url: str):

        nm_id = self.extract_nm_id(url)

        if nm_id is None:
            raise WildberriesParsingError("Не удалось определить артикул.")

        api = (
            "https://card.wb.ru/cards/v2/detail"
            f"?appType=1&curr=rub&dest=-1257786&spp=30&nm={nm_id}"
        )

        headers = {
            "User-Agent": "Mozilla/5.0"
        }

        async with aiohttp.ClientSession(headers=headers) as session:

            async with session.get(api) as response:

                if response.status != 200:
                    raise WildberriesParsingError(
                        f"Ошибка Wildberries ({response.status})"
                    )

                data = await response.json(content_type=None)

        products = data.get("data", {}).get("products", [])

        if not products:
            raise WildberriesParsingError("Товар не найден.")

        product = products[0]

        price = product.get("salePriceU")

        if price is not None:
            price = price / 100

        return WildberriesProduct(
            nm_id=product.get("id"),
            name=product.get("name", ""),
            price=price,
            rating=product.get("reviewRating") or product.get("rating"),
            feedbacks_count=product.get("feedbacks"),
        )