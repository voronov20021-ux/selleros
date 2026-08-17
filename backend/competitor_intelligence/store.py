"""
Persistent cache конкурентного evidence в IntelligenceStore.

Не хранит бесконечно: TTL = CostGuard CACHE_TTL_DAYS (7).
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from backend.intelligence.cost_guard import CACHE_TTL_DAYS

_TTL_SECONDS = CACHE_TTL_DAYS * 86400.0


class CompetitorEvidenceStore:
    """CRUD поверх той же SQLite, что IntelligenceStore."""

    def __init__(self, intel_store: Any) -> None:
        self._store = intel_store

    @property
    def db(self):
        return self._store.db

    async def save_rows(
        self,
        *,
        query: str,
        product_id: str,
        rows: list[dict[str, Any]],
        source: str = "yandex_search",
        ttl_seconds: float | None = None,
    ) -> None:
        now = time.time()
        ttl = _TTL_SECONDS if ttl_seconds is None else float(ttl_seconds)
        expires = now + ttl
        for row in rows:
            cid = str(row.get("competitor_id") or "")
            if not cid:
                continue
            await self.db.execute(
                """
                INSERT OR REPLACE INTO competitor_evidence_cache
                    (id, query, product_id, competitor_id, source, data,
                     retrieved_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    query,
                    str(product_id),
                    cid,
                    source,
                    json.dumps(row, ensure_ascii=False),
                    float(row.get("retrieved_at") or now),
                    expires,
                ),
            )
        await self.db.commit()

    async def load_fresh(
        self,
        *,
        query: str,
        product_id: str,
    ) -> list[dict[str, Any]]:
        now = time.time()
        cursor = await self.db.execute(
            """
            SELECT competitor_id, source, data, retrieved_at, expires_at
            FROM competitor_evidence_cache
            WHERE query = ? AND product_id = ? AND expires_at > ?
              AND competitor_id != '__meta__'
            ORDER BY retrieved_at DESC
            """,
            (query, str(product_id), now),
        )
        rows = await cursor.fetchall()
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            cid = row[0]
            if cid in seen:
                continue
            seen.add(cid)
            try:
                data = json.loads(row[2] or "{}")
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                data.setdefault("competitor_id", cid)
                data.setdefault("source", row[1])
                out.append(data)
        return out

    async def save_meta(
        self,
        *,
        query: str,
        product_id: str,
        meta: dict[str, Any],
    ) -> None:
        now = time.time()
        await self.db.execute(
            """
            INSERT OR REPLACE INTO competitor_evidence_cache
                (id, query, product_id, competitor_id, source, data,
                 retrieved_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                query,
                str(product_id),
                "__meta__",
                "competitor_intel",
                json.dumps(meta, ensure_ascii=False),
                now,
                now + _TTL_SECONDS,
            ),
        )
        await self.db.commit()

    async def save_snapshots(self, rows: list[dict[str, Any]]) -> None:
        """Append-only коммерческий snapshot. Не live-мониторинг."""
        now = time.time()
        for row in rows or []:
            cid = str(row.get("competitor_id") or "")
            if not cid or cid == "__meta__":
                continue
            price = row.get("price")
            rating = row.get("rating")
            feedbacks = row.get("feedbacks")
            if price is None and rating is None and feedbacks is None:
                continue
            try:
                price_i = int(price) if price is not None else None
            except (TypeError, ValueError):
                price_i = None
            try:
                rating_f = float(rating) if rating is not None else None
            except (TypeError, ValueError):
                rating_f = None
            try:
                fb_i = int(feedbacks) if feedbacks is not None else None
            except (TypeError, ValueError):
                fb_i = None
            await self.db.execute(
                """
                INSERT INTO competitor_snapshots
                    (id, competitor_id, query, product_id, price, rating,
                     feedbacks, captured_at, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    cid,
                    str(row.get("query") or ""),
                    str(row.get("product_id") or ""),
                    price_i,
                    rating_f,
                    fb_i,
                    float(row.get("captured_at") or now),
                    str(row.get("source") or "card.wb.ru"),
                ),
            )
        await self.db.commit()

    async def load_snapshots(
        self,
        competitor_id: str,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        cursor = await self.db.execute(
            """
            SELECT competitor_id, price, rating, feedbacks, captured_at, source
            FROM competitor_snapshots
            WHERE competitor_id = ?
            ORDER BY captured_at DESC
            LIMIT ?
            """,
            (str(competitor_id), int(limit)),
        )
        rows = await cursor.fetchall()
        return [
            {
                "competitor_id": row[0],
                "price": row[1],
                "rating": row[2],
                "feedbacks": row[3],
                "captured_at": row[4],
                "source": row[5],
            }
            for row in rows
        ]
