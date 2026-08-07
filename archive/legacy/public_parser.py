import aiohttp
import asyncio


async def get_product(nm_id):

    url = (
        "https://card.wb.ru/cards/v2/detail"
    )

    params = {
        "appType": 1,
        "curr": "rub",
        "dest": -1257786,
        "nm": nm_id
    }


    headers = {
        "User-Agent":
        "Mozilla/5.0"
    }


    async with aiohttp.ClientSession(
        headers=headers
    ) as session:

        async with session.get(
            url,
            params=params
        ) as response:

            print(
                "STATUS:",
                response.status
            )

            data = await response.json()

            return data



async def main():

    product = await get_product(
        781694918
    )

    print(product)



if __name__ == "__main__":
    asyncio.run(main())