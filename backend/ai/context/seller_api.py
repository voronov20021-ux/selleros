"""
seller_api.py — ЗАГОТОВКА источника статистики продавца.

Реализации пока нет — по плану проекта Seller API подключается позже.
Здесь только каркас, чтобы подключение свелось к одной строке.

Когда появится ключ Seller API:
    1. заполнить fetch() данными из backend/providers/seller_stats.py;
    2. в bot.py добавить:
       context_builder.register(SellerStatsContextSource(stats_provider))

Ни Brain, ни промпты, ни хендлеры менять не придётся.
"""

from backend.ai.context.base import ContextBlock, ContextRequest, ContextSource
from backend.ai.intents import Intent


class SellerStatsContextSource(ContextSource):

    name = "seller_stats"
    intents = frozenset({
        Intent.SELLER_ANALYTICS,
        Intent.PRICING,
        Intent.MARKETING,
        Intent.LOGISTICS,
    })

    def __init__(self, stats_provider=None):
        self.stats = stats_provider

    async def fetch(self, request: ContextRequest) -> ContextBlock | None:
        if self.stats is None or not await self.stats.is_available():
            return None

        # TODO: заказы, выручка, остатки по складам, CTR, CR, расходы.
        # Модели данных уже описаны в backend/providers/seller_stats.py.
        return None
