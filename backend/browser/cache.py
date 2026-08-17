"""
Публичный product cache для BrowserProvider.

Ключ: nm/article (+ kind). Без user_id — общий для всех продавцов.
Seller-specific данные сюда не пишутся.

Статусы:
  HIT   — свежий → 0 browser
  STALE — истёк TTL → нужен refresh (не считается HIT)
  MISS  — нет записи
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

from backend.browser.serialize import product_from_public_dict, product_to_public_dict
from backend.wb.cdn_provider import WBProduct

log = logging.getLogger("selleros.browser.cache")

T = TypeVar("T")


class CacheStatus(str, Enum):
    HIT = "HIT"
    STALE = "STALE"
    MISS = "MISS"


class PublicProductCache:
    """
    Диск + single-flight на article.

    get_fresh → только HIT (stale удаляется из «свежих», но запись остаётся
    для диагностики/опционального чтения через peek_stale).
    """

    KIND_PRODUCT = "product"
    #: snapshot/provider type в ключе — stale browser не путаем с другими видами
    PROVIDER_BROWSER = "browser"

    def __init__(
        self,
        db_path: str,
        *,
        ttl_product: float = 7 * 86400,
        ttl_photos: float | None = None,
        ttl_description: float | None = None,
        provider: str = PROVIDER_BROWSER,
    ):
        self.db_path = db_path
        self.ttl_product = float(ttl_product)
        # Зарезервировано под раздельные TTL (MVP: используем product TTL).
        self.ttl_photos = float(ttl_photos if ttl_photos is not None else ttl_product)
        self.ttl_description = float(
            ttl_description if ttl_description is not None else ttl_product
        )
        self.provider = (provider or self.PROVIDER_BROWSER).strip() or self.PROVIDER_BROWSER
        self._pending: dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()
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
                CREATE TABLE IF NOT EXISTS public_product_cache (
                    cache_key TEXT PRIMARY KEY,
                    article INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                )
                """
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_ppc_article "
                "ON public_product_cache(article, kind)"
            )
            con.commit()

    def key_for(self, article: int, kind: str = KIND_PRODUCT) -> str:
        """Ключ: nm_id + kind + provider/snapshot type."""
        return f"wb:{int(article)}:{kind}:{self.provider}"

    @staticmethod
    def legacy_key_for(article: int, kind: str = KIND_PRODUCT) -> str:
        """Старый ключ без provider — для чтения legacy HIT."""
        return f"wb:{int(article)}:{kind}"

    def _row_for(self, article: int, kind: str = KIND_PRODUCT):
        """Новый ключ, иначе legacy (без provider)."""
        keys = (self.key_for(article, kind), self.legacy_key_for(article, kind))
        with self._connect() as con:
            for key in keys:
                row = con.execute(
                    "SELECT cache_key, payload, created_at, expires_at "
                    "FROM public_product_cache WHERE cache_key=?",
                    (key,),
                ).fetchone()
                if row is not None:
                    return row
        return None

    def inspect(self, article: int, kind: str = KIND_PRODUCT) -> CacheStatus:
        """HIT / STALE / MISS без удаления."""
        row = self._row_for(article, kind)
        if row is None:
            return CacheStatus.MISS
        if time.time() > float(row["expires_at"]):
            return CacheStatus.STALE
        return CacheStatus.HIT

    def get_fresh(self, article: int, kind: str = KIND_PRODUCT) -> WBProduct | None:
        """Только свежий HIT. Stale → None (нужен browser refresh)."""
        row = self._row_for(article, kind)
        if row is None:
            return None
        if time.time() > float(row["expires_at"]):
            return None
        try:
            data = json.loads(row["payload"])
            product = product_from_public_dict(data)
            if not getattr(product, "source", None) or product.source == "live":
                product.source = "browser_cache"
            return product
        except Exception as exc:
            log.warning("Browser cache corrupt article=%s: %s", article, exc)
            return None

    def peek_any(self, article: int, kind: str = KIND_PRODUCT) -> WBProduct | None:
        """Вернуть запись даже если stale (для диагностики). Не HIT."""
        row = self._row_for(article, kind)
        if row is None:
            return None
        try:
            return product_from_public_dict(json.loads(row["payload"]))
        except Exception:
            return None

    def set_product(
        self,
        product: WBProduct,
        *,
        ttl: float | None = None,
        kind: str = KIND_PRODUCT,
    ) -> None:
        article = int(product.article)
        key = self.key_for(article, kind)
        legacy = self.legacy_key_for(article, kind)
        now = time.time()
        ttl_s = float(ttl if ttl is not None else self.ttl_product)
        payload = json.dumps(product_to_public_dict(product), ensure_ascii=False)
        with self._connect() as con:
            for k in (key, legacy):
                con.execute(
                    """
                    INSERT INTO public_product_cache
                        (cache_key, article, kind, payload, created_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        payload=excluded.payload,
                        created_at=excluded.created_at,
                        expires_at=excluded.expires_at
                    """,
                    (k, article, kind, payload, now, now + ttl_s),
                )
            con.commit()

    def invalidate(self, article: int, kind: str = KIND_PRODUCT) -> None:
        keys = (self.key_for(article, kind), self.legacy_key_for(article, kind))
        with self._connect() as con:
            for key in keys:
                con.execute(
                    "DELETE FROM public_product_cache WHERE cache_key=?", (key,),
                )
            con.commit()

    def force_expire(self, article: int, kind: str = KIND_PRODUCT) -> None:
        """Пометить запись как stale (для тестов)."""
        keys = (self.key_for(article, kind), self.legacy_key_for(article, kind))
        with self._connect() as con:
            for key in keys:
                con.execute(
                    "UPDATE public_product_cache SET expires_at=? WHERE cache_key=?",
                    (time.time() - 1.0, key),
                )
            con.commit()

    async def get_or_fetch(
        self,
        article: int,
        fetch: Callable[[], Awaitable[WBProduct | None]],
        *,
        ttl: float | None = None,
    ) -> tuple[CacheStatus, WBProduct | None]:
        """
        Single-flight на article: один fetch, остальные ждут Future.

        Возвращает (входной статус HIT|STALE|MISS, product).
        Для waiter'ов после чужого успеха статус = HIT.
        """
        article = int(article)
        key = self.key_for(article)
        is_leader = False
        future: asyncio.Future | None = None

        async with self._lock:
            status = self.inspect(article)
            if status == CacheStatus.HIT:
                product = self.get_fresh(article)
                if product is not None:
                    return CacheStatus.HIT, product
                status = CacheStatus.MISS

            pending = self._pending.get(key)
            if pending is not None:
                future = pending
            else:
                loop = asyncio.get_running_loop()
                future = loop.create_future()
                self._pending[key] = future
                is_leader = True

        assert future is not None

        if not is_leader:
            product = await future
            return CacheStatus.HIT, product

        try:
            # Ещё раз под страховкой (другой процесс/диск)
            status2 = self.inspect(article)
            if status2 == CacheStatus.HIT:
                product = self.get_fresh(article)
                if product is not None:
                    if not future.done():
                        future.set_result(product)
                    return CacheStatus.HIT, product

            product = await fetch()
            if product is not None:
                self.set_product(product, ttl=ttl)
            if not future.done():
                future.set_result(product)
            return status, product
        except Exception:
            # Ждущие получают None (graceful), лидер пробрасывает ошибку выше.
            if not future.done():
                future.set_result(None)
            raise
        finally:
            async with self._lock:
                self._pending.pop(key, None)

