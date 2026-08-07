import asyncio
import time

from backend.services.wb_service import WBService


QUERIES = [
    "Redmi Note 15",
    "iPhone 15",
    "Nike кроссовки",
    "кофемашина",
    "наушники bluetooth",
    "умные часы",
    "футболка мужская",
    "кроссовки мужские",
    "рюкзак городской",
    "чехол для телефона",
    "пылесос",
    "клавиатура механическая",
    "монитор 27",
    "игровая мышь",
    "колонка bluetooth",
    "зарядка type c",
    "стул офисный",
    "лампа настольная",
    "термос",
    "диван"
]


async def user_task(
    service,
    number,
    query
):

    start = time.time()

    try:

        products = await service.search(
            query
        )

        print(
            number,
            "OK |",
            query,
            "| товаров:",
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
            "ERROR |",
            query,
            "|",
            e
        )



async def main():

    print(
        "=== ТЕСТ 20 РАЗНЫХ ПОЛЬЗОВАТЕЛЕЙ ==="
    )


    service = WBService()

    await service.start()


    tasks = []


    for i, query in enumerate(QUERIES):

        tasks.append(
            user_task(
                service,
                i,
                query
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