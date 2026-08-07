import asyncio
from backend.wb.parser import WildberriesPublicProvider


async def main():
    parser = WildberriesPublicProvider()

    result = await parser.get_product(
        "https://www.wildberries.ru/catalog/781694918/detail.aspx"
    )

    print(result)


if __name__ == "__main__":
    asyncio.run(main())