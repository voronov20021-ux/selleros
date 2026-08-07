"""
Минимальный публичный провайдер данных о товаре Wildberries.

Берёт:
- название
- цену
- рейтинг
- отзывы

Использует браузерную сессию WB через Playwright,
чтобы получить реальные cookie.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final

import aiohttp

from backend.wb.browser_session import get_wb_cookies


CARD_LIST_URL: Final = (
    "https://www.wildberries.ru/__internal/u-card/cards/v4/list"
)

NM_ID_RE: Final = re.compile(r"/catalog/(\d+)/")


HEADERS: Final = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8",
    "Referer": "https://www.wildberries.ru/",
    "Origin": "https://www.wildberries.ru",
}


class WildberriesParsingError(Exception):
    pass


@dataclass(slots=True)
class WildberriesProduct:
    nm_id: int
    name: str
    price: float | None
    rating: float | None
    feedbacks_count: int | None



class WildberriesPublicProvider:


    def extract_nm_id(self, url: str) -> int | None:

        url = url.strip()

        if url.isdigit():
            return int(url)

        match = NM_ID_RE.search(url)

        if match:
            return int(match.group(1))

        return None



    async def get_product(self, url: str) -> WildberriesProduct:

        nm_id = self.extract_nm_id(url)

        if nm_id is None:
            raise WildberriesParsingError(
                f"Не найден ID товара: {url}"
            )


        params = {
            "appType": "1",
            "curr": "rub",
            "dest": "-1257786",
            "spp": "30",
            "hide_vflags": "4294967296",
            "hide_dtype": "15",
            "lang": "ru",
            "ab_testing": "false",
            "nm": str(nm_id),
        }


        # Получаем настоящие cookie WB через браузер
        cookies = await get_wb_cookies()


        try:

            async with aiohttp.ClientSession(
                headers=HEADERS,
                cookies=cookies
            ) as session:


                async with session.get(
                    CARD_LIST_URL,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:


                    print("WB статус:", resp.status)


                    if resp.status != 200:

                        text = await resp.text()

                        raise WildberriesParsingError(
                            f"WB вернул {resp.status}: {text[:300]}"
                        )


                    payload = await resp.json(
                        content_type=None
                    )


        except aiohttp.ClientError as e:

            raise WildberriesParsingError(
                f"Ошибка сети: {e}"
            )


        products = (
            payload.get("products")
            if isinstance(payload, dict)
            else []
        )


        if not products:

            raise WildberriesParsingError(
                f"Товар {nm_id} не найден"
            )


        return self._parse(products[0])



    def _parse(
        self,
        item: dict[str, Any]
    ) -> WildberriesProduct:


        return WildberriesProduct(

            nm_id=item.get("id"),

            name=item.get(
                "name",
                ""
            ),

            price=self._extract_price(
                item
            ),

            rating=(
                item.get("reviewRating")
                or item.get("rating")
            ),

            feedbacks_count=item.get(
                "feedbacks"
            )
        )



    def _extract_price(
        self,
        item: dict[str, Any]
    ) -> float | None:


        for size in item.get("sizes", []):

            price = size.get("price")

            if price and price.get("product"):

                return round(
                    price["product"] / 100,
                    2
                )


        return None