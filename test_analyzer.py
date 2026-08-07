import asyncio

from backend.wb.browser_provider import WBProvider
from backend.analyzer.card_analyzer import CardAnalyzer


async def main():

    nm_id = 781694912

    print("Получаем товар WB...\n")


    provider = WBProvider()

    product = await provider.get_product(
        nm_id
    )


    print("\n=== ДАННЫЕ ТОВАРА ===")
    print(product)


    print("\n=== АНАЛИЗ ===")


    analyzer = CardAnalyzer()

    result = analyzer.analyze(
        product
    )


    print(
        result
    )


if __name__ == "__main__":
    asyncio.run(main())