"""
ProductService — ГЛАВНОЕ место получения товара в проекте.

Правило проекта:
    Хендлеры и сервисы НЕ знают про BrowserPool, парсеры и API.
    Товар берётся только так:

        product = await product_service.get_product("wildberries", article)

Внутри — список провайдеров по маркетплейсам.
Провайдеры опрашиваются по очереди (по приоритету):
кто первый ответил — того и товар.

Заменить источник = зарегистрировать другой провайдер. Одна строка.
"""

import logging

from backend.providers.base import ProductProvider
from backend.wb.cdn_provider import WBProduct

log = logging.getLogger("selleros.products")


class ProductService:

    def __init__(self):
        # {"wildberries": [провайдер1, провайдер2], "ozon": [...]}
        self._providers: dict[str, list[ProductProvider]] = {}

    def register(self, provider: ProductProvider, priority: int = 10):
        """
        Добавить источник товаров.
        Чем меньше priority — тем раньше опрашивается.
        """
        chain = self._providers.setdefault(provider.marketplace, [])
        chain.append((priority, provider))
        chain.sort(key=lambda item: item[0])

        log.info(
            "Провайдер подключён: %s (%s, приоритет %d)",
            provider.name, provider.marketplace, priority,
        )

    async def get_product(
        self,
        marketplace: str,
        article: int,
    ) -> WBProduct | None:
        marketplace = marketplace.lower()

        chain = self._providers.get(marketplace, [])

        if not chain:
            log.warning("Нет провайдеров для %s", marketplace)
            return None

        for _, provider in chain:

            if not await provider.is_available():
                continue

            product = await provider.get_product(article)

            if product is not None:
                log.info(
                    "Товар %s получен через %s",
                    article, provider.name,
                )
                return product

        log.info("Товар %s не найден ни в одном источнике", article)
        return None
