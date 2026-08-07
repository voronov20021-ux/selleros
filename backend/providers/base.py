"""
ProductProvider — интерфейс источника товаров.

Это главная точка расширения проекта:

    сегодня     -> WBBrowserProvider   (BrowserPool, резервный путь)
    завтра      -> WBSellerAPIProvider (официальный Seller API WB)
    послезавтра -> OzonProvider, AvitoProvider, ...

Весь остальной код знает только этот интерфейс
и класс WBProduct. Откуда пришли данные — ему всё равно.
"""

from abc import ABC, abstractmethod

from backend.wb.cdn_provider import WBProduct


class ProductProvider(ABC):

    #: Имя источника — для логов, пользователю не показывается.
    name: str = "base"

    #: Какой маркетплейс обслуживает: "wildberries" | "ozon" | "avito"
    marketplace: str = "wildberries"

    @abstractmethod
    async def get_product(self, article: int) -> WBProduct | None:
        """Вернуть нормализованный WBProduct или None, если не нашли."""
        raise NotImplementedError

    async def is_available(self) -> bool:
        """Жив ли источник. По умолчанию считаем, что жив."""
        return True
