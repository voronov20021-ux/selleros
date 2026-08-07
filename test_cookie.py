import asyncio
from backend.wb.browser_session import get_wb_cookies


async def main():
    cookies = await get_wb_cookies()
    print(cookies)


asyncio.run(main())