"""
WBBrowserProvider — адаптер над WB Engine для ProductService.

Имя файла и класса — историческое (раньше здесь был BrowserPool),
менять не стал, чтобы не трогать ProductService и bot.py лишний раз.
По сути это теперь просто тонкий переходник к WBEngine — вся
логика получения данных (перебор источников, кэш, остывание после
429) живёт в backend/wb_engine/.

normalize_raw() оставлена на месте для обратной совместимости —
раньше источник (старый BrowserPool) отдавал сырой dict, который
нужно было превращать в WBProduct. WBEngine теперь отдаёт уже
готовый WBProduct, так что нормализация в get_product() почти
не нужна — но функция может пригодиться, если когда-нибудь
понадобится подключить источник, отдающий сырые данные напрямую,
в обход WBEngine.
"""

import logging

from backend.providers.base import ProductProvider
from backend.wb.cdn_provider import BasketResolver, WBProduct, apply_detail
from backend.wb_engine import WBEngine

log = logging.getLogger("selleros.provider.wb_browser")

_baskets = BasketResolver(cache_path=None)


def normalize_raw(article: int, raw) -> WBProduct | None:
    """Сырой ответ card.wb.ru v2 -> WBProduct. Для обратной совместимости."""

    if isinstance(raw, WBProduct):
        return raw

    if not isinstance(raw, dict):
        log.warning("Неожиданный тип сырых данных: %s", type(raw))
        return None

    product = WBProduct(article=article)
    product.basket = f"{_baskets.predict(article // 100000):02d}"
    apply_detail(product, raw)
    return product


class WBBrowserProvider(ProductProvider):

    name = "wb_engine"
    marketplace = "wildberries"

    def __init__(self, engine: WBEngine):
        self.engine = engine

    async def get_product(self, article: int) -> WBProduct | None:
        try:
            return await self.engine.get_product(article)
        except Exception as error:
            # Двойная страховка: WBEngine и сам не должен пропускать
            # исключения наружу (это его главная задача), но если
            # где-то в новом источнике всё же есть баг — ProductService
            # должен получить честный None, а не упасть.
            log.exception("WB Engine неожиданно упал для товара %s: %s", article, error)
            return None
