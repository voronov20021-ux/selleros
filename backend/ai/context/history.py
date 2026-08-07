"""
history.py — контекст из истории анализов продавца.

Даёт Seller AI понимание масштаба: сколько товаров разобрано,
какой средний уровень карточек, что уже смотрели.
"""

import time

from backend.ai.context.base import ContextBlock, ContextRequest, ContextSource
from backend.ai.intents import Intent


class AnalysisHistorySource(ContextSource):

    name = "analysis_history"
    intents = frozenset({
        Intent.SELLER_ANALYTICS,
        Intent.COMPETITOR,
        Intent.GENERAL_QUESTION,
    })

    def __init__(self, history):
        self.history = history

    async def fetch(self, request: ContextRequest) -> ContextBlock | None:
        items = await self.history.list(request.user_id, limit=5)

        if not items:
            return None

        summary = await self.history.summary(request.user_id, "month")

        lines = []
        if summary:
            lines.append(
                f"Разобрано карточек за месяц: {summary['count']}, "
                f"средняя оценка {summary['avg_score']}/100."
            )
            lines.append("")

        lines.append("Последние разборы:")
        for item in items:
            when = time.strftime("%d.%m", time.localtime(item["time"]))
            price = f", {item['price']} руб." if item.get("price") else ""
            lines.append(
                f"- {when}: {item['title'][:40]} — {item['score']}/100{price}"
            )

        return ContextBlock(
            title="ИСТОРИЯ РАБОТЫ ПРОДАВЦА",
            body="\n".join(lines),
            priority=40,
        )
