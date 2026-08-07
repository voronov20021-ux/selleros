import asyncio

from backend.wb.browser_provider import WBProvider



async def main():

    wb = WBProvider()


    product = await wb.get_product(
        781694918
    )


    print("\n================")
    print(product)
    print("================")



asyncio.run(main())