from playwright.sync_api import sync_playwright


def inspect_wb_page(url: str):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)

        page = browser.new_page()

        def log_response(response):
            response_url = response.url.lower()

            if (
                "card.wb.ru" in response_url
                or "basket" in response_url
                or "catalog" in response_url
            ):
                print("\n===================================")
                print("STATUS:", response.status)
                print("URL:", response.url)

                try:
                    print("BODY:")
                    print(response.text()[:3000])
                except Exception:
                    print("Не удалось прочитать ответ")

        page.on("response", log_response)

        page.goto(url, wait_until="networkidle")

        input("\nНажми Enter для завершения...")

        browser.close()