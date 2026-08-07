from backend.core.browser_pool import browser_pool


async def get_product_info(article: str):

    product = await browser_pool.get_product(int(article))

    if product is None:
        return {
            "article": article,
            "name": "Товар не найден",
            "brand": "-",
            "price": "-",
            "rating": "-",
            "reviews": "-",
        }

    return {
        "article": product.id,
        "name": product.name,
        "brand": product.brand,
        "price": "-",
        "rating": product.rating,
        "reviews": product.reviews,
    }