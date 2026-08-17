import re

from backend.api.miniapp_catalog import parse_article_input


def parse_marketplace_link(link: str):
    """
    Возвращает:
    (marketplace, article)

    marketplace:
        "Wildberries"
        "Ozon"
        None

    Same nmID / WB URL pipeline as Mini App ``parse_article_input``.
    """

    link = (link or "").strip()
    if not link:
        return None, None

    # Ozon before WB: ozon URLs end with -skuId
    if "ozon" in link.lower():
        ozon = re.search(r"-([0-9]+)/?$", link)
        if ozon:
            return "Ozon", ozon.group(1)

    article = parse_article_input(link)
    if article:
        return "Wildberries", str(article)

    return None, None