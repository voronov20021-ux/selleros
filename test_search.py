import asyncio

from backend.wb.search_provider import WBSearchProvider



async def main():

    provider = WBSearchProvider()


    products = await provider.search(
        "Redmi Note 15"
    )


    print("\nНАЙДЕНО:", len(products))


    for p in products:

        print(
            p.id,
            "|",
            p.name,
            "|",
            p.rating,
            "|",
            p.reviews
        )



if __name__ == "__main__":
    asyncio.run(main())