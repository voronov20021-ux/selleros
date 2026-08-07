import asyncio
import aiohttp


async def main():

    url = "https://search.wb.ru/exactmatch/ru/common/v18/search"

    params = {
        "appType": "1",
        "curr": "rub",
        "dest": "-1257786",
        "lang": "ru",
        "page": "1",
        "query": "Redmi Note 15",
        "resultset": "catalog",
        "sort": "popular",
        "spp": "30",
    }


    async with aiohttp.ClientSession() as session:

        async with session.get(
            url,
            params=params
        ) as r:

            print("STATUS:", r.status)

            text = await r.text()

            print(text[:1000])


asyncio.run(main())