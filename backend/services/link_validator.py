def validate_marketplace_link(link: str) -> bool:
    link = link.lower().strip()

    return (
        "wildberries.ru" in link
        or "wb.ru" in link
        or "ozon.ru" in link
    )