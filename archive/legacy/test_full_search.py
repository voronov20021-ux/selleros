import asyncio

from backend.services.wb_service import WBService
from backend.wb.search_provider import WBSearchProvider


async def main():

    provider = WBSearchProvider()

    service = WBService(
        search_provider=provider
    )

    await service.start()


    tasks = []


    for i in range(20):

        tasks.append(
            service.search(
                "Redmi Note 15"
            )
        )


    results = await asyncio.gather(
        *tasks,
        return_exceptions=True
    )


    for i, result in enumerate(results):

        if isinstance(result, Exception):

            print(
                i,
                "ОШИБКА:",
                result
            )

        else:

            print(
                i,
                "OK:",
                len(result)
            )



if __name__ == "__main__":

    asyncio.run(main())