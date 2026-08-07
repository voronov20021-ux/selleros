import re


def parse_marketplace_link(link: str):
    """
    Возвращает:
    (marketplace, article)

    marketplace:
        "Wildberries"
        "Ozon"
        None
    """

    link = link.strip()

    # Wildberries
    wb = re.search(r"/catalog/(\d+)", link)
    if wb:
        return "Wildberries", wb.group(1)

    # Ozon
    ozon = re.search(r"-([0-9]+)/?$", link)
    if ozon:
        return "Ozon", ozon.group(1)

    return None, None