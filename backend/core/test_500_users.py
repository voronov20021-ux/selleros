import asyncio
import time

from backend.services.wb_service import WBService



async def user_task(
    service,
    number
):

    start = time.time()


    try:

        products = await service.search(
            "Redmi Note 15"
        )


        print(
            number,
            "OK | товаров:",
            len(products),
            "| время:",
            round(
                time.time() - start,
                2
            ),
            "сек"
        )


    except Exception as e:

        print(
            number,
            "ERROR:",
            e
        )



async def main():


    print(
        "=== ТЕСТ 20 ПОЛЬЗОВАТЕЛЕЙ ==="
    )


    service = WBService()


    await service.start()



    tasks = []


    for i in range(20):

        tasks.append(
            user_task(
                service,
                i
            )
        )


    await asyncio.gather(
        *tasks
    )



    print(
        "=== ТЕСТ ЗАКОНЧЕН ==="
    )



if __name__ == "__main__":

    asyncio.run(main())