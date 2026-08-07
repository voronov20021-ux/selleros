"""
HistoryService — история анализов пользователя.

С этого этапа — тонкая обёртка над MemoryStore (долговременная
память, файл SQLite на диске). Раньше данные хранились в списке
в оперативной памяти и пропадали при перезапуске бота — теперь нет.

ВАЖНО: форма данных, которую возвращают list()/summary()/for_period(),
НЕ ИЗМЕНИЛАСЬ — те же ключи "time", "article", "title", "price",
"score", "verdict". Поэтому весь код, который эти данные читает
(экран истории, отчёты), трогать не пришлось — только добавить await.
"""

from __future__ import annotations

import time

from backend.memory import AnalysisRecord, MemoryStore

# Периоды для раздела «📅 Отчёты», в секундах.
PERIODS = {
    "day": 24 * 3600,
    "week": 7 * 24 * 3600,
    "month": 30 * 24 * 3600,
    "year": 365 * 24 * 3600,
}

PERIOD_TITLES = {
    "day": "за день",
    "week": "за неделю",
    "month": "за месяц",
    "year": "за год",
}


class HistoryService:

    def __init__(self, memory_store: MemoryStore):
        self.store = memory_store

    async def add(
        self,
        user_id: int,
        article: int,
        title: str,
        score: int,
        price: int | None = None,
        verdict: str = "",
    ) -> None:
        await self.store.add_analysis(
            user_id=user_id,
            article=article,
            marketplace="wildberries",
            title=title,
            price=price,
            score=score,
            verdict=verdict,
        )

    async def list(self, user_id: int, limit: int = 10) -> list[dict]:
        records = await self.store.list_analyses(user_id, limit=limit)
        return [_to_dict(r) for r in records]

    async def for_period(self, user_id: int, period: str) -> list[dict]:
        """Записи за период: day | week | month | year."""
        seconds = PERIODS.get(period, PERIODS["day"])
        border = time.time() - seconds

        records = await self.store.analyses_since(user_id, border)
        return [_to_dict(r) for r in records]

    async def summary(self, user_id: int, period: str) -> dict | None:
        """Сводка за период для отчёта. None — если анализов не было."""
        items = await self.for_period(user_id, period)

        if not items:
            return None

        scores = [item["score"] for item in items]

        return {
            "count": len(items),
            "avg_score": round(sum(scores) / len(scores)),
            "best": max(items, key=lambda item: item["score"]),
            "worst": min(items, key=lambda item: item["score"]),
            "items": items[:5],
        }


def _to_dict(record: AnalysisRecord) -> dict:
    """AnalysisRecord -> старый формат dict, который уже понимает весь проект."""
    return {
        "time": record.created_at,
        "article": record.article,
        "title": record.title,
        "price": record.price,
        "score": record.score,
        "verdict": record.verdict,
    }
