"""
Публичный кэш текстов отзывов (отдельно от product cache и seller memory).

Ключ: article (+ optional imt). Без user_id.
Seller-entered feedbacks count сюда не пишется.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from backend.services.wb_reviews import Review, review_fingerprint

log = logging.getLogger("selleros.browser.reviews_cache")


class PublicReviewsCache:
    def __init__(self, db_path: str, *, ttl: float = 7 * 86400):
        self.db_path = db_path
        self.ttl = float(ttl)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS public_reviews_cache (
                    cache_key TEXT PRIMARY KEY,
                    article INTEGER NOT NULL,
                    imt_id INTEGER,
                    payload TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                )
                """
            )
            con.commit()

    @staticmethod
    def key_for(article: int) -> str:
        return f"wb:reviews:{int(article)}"

    def inspect(self, article: int) -> str:
        key = self.key_for(article)
        with self._connect() as con:
            row = con.execute(
                "SELECT expires_at FROM public_reviews_cache WHERE cache_key=?",
                (key,),
            ).fetchone()
        if row is None:
            return "MISS"
        if time.time() > float(row["expires_at"]):
            return "STALE"
        return "HIT"

    def get_fresh(self, article: int) -> list[Review] | None:
        """HIT → list (может быть пустым). STALE/MISS → None."""
        key = self.key_for(article)
        with self._connect() as con:
            row = con.execute(
                "SELECT payload, expires_at FROM public_reviews_cache WHERE cache_key=?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        if time.time() > float(row["expires_at"]):
            return None
        try:
            raw = json.loads(row["payload"])
            if not isinstance(raw, list):
                return []
            return [_review_from_dict(item, article) for item in raw if isinstance(item, dict)]
        except Exception as exc:
            log.warning("reviews cache corrupt article=%s: %s", article, exc)
            return None

    def set_reviews(
        self,
        article: int,
        reviews: list[Review],
        *,
        imt_id: int | None = None,
        ttl: float | None = None,
    ) -> None:
        key = self.key_for(article)
        now = time.time()
        ttl_s = float(ttl if ttl is not None else self.ttl)
        payload = json.dumps(
            [_review_to_dict(r) for r in reviews],
            ensure_ascii=False,
        )
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO public_reviews_cache
                    (cache_key, article, imt_id, payload, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    imt_id=excluded.imt_id,
                    payload=excluded.payload,
                    created_at=excluded.created_at,
                    expires_at=excluded.expires_at
                """,
                (key, int(article), imt_id, payload, now, now + ttl_s),
            )
            con.commit()

    def force_expire(self, article: int) -> None:
        key = self.key_for(article)
        with self._connect() as con:
            con.execute(
                "UPDATE public_reviews_cache SET expires_at=? WHERE cache_key=?",
                (time.time() - 1.0, key),
            )
            con.commit()

    def invalidate(self, article: int) -> None:
        key = self.key_for(article)
        with self._connect() as con:
            con.execute(
                "DELETE FROM public_reviews_cache WHERE cache_key=?", (key,),
            )
            con.commit()


def _review_to_dict(r: Review) -> dict[str, Any]:
    return {
        "review_id": r.review_id,
        "article_id": r.article_id,
        "text": r.text,
        "rating": r.rating,
        "created_at": r.created_at,
        "source_url": r.source_url,
        "fingerprint": r.fingerprint or review_fingerprint(r.text),
        "metadata": r.metadata or {},
    }


def _review_from_dict(d: dict[str, Any], article: int) -> Review:
    text = str(d.get("text") or "")
    return Review(
        review_id=str(d.get("review_id") or d.get("id") or review_fingerprint(text)),
        article_id=int(d.get("article_id") or article),
        text=text,
        rating=d.get("rating"),
        created_at=d.get("created_at"),
        source_url=d.get("source_url"),
        fingerprint=str(d.get("fingerprint") or review_fingerprint(text)),
        metadata=dict(d.get("metadata") or {}),
    )
