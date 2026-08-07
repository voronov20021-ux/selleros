from playwright.async_api import async_playwright


async def get_wb_cookies():

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=False
        )

        page = await browser.new_page()

        await page.goto(
            "https://www.wildberries.ru/",
            wait_until="domcontentloaded",
            timeout=60000
        )

        await page.wait_for_timeout(5000)

        print("TITLE:", await page.title())
        print("URL:", page.url)

        cookies = await page.context.cookies()

        print("COOKIES:", cookies)

        await browser.close()

        return {
            c["name"]: c["value"]
            for c in cookies
        }