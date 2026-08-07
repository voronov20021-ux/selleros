"""
store.py — долговременная память ARGUS.

Один класс, который знает, как хранить данные. Весь остальной проект
не знает про SQLite вообще — только про методы MemoryStore.

Почему SQLite, а не сразу PostgreSQL:
    SQLite — это просто файл на диске. Не нужно ставить сервер,
    настраивать доступы, поднимать docker. Для одного бота с
    умеренной нагрузкой этого достаточно с запасом.

Как будем переезжать на PostgreSQL позже:
    SQL-запросы здесь написаны на обычном ANSI SQL, без диалектных
    трюков SQLite (кроме INSERT ... ON CONFLICT, который есть и в
    PostgreSQL). Переезд — это замена aiosqlite на asyncpg внутри
    ЭТОГО ОДНОГО файла. Ни одна другая часть проекта не изменится,
    потому что все обращаются только к методам MemoryStore.

Таблицы (см. models.py для точной формы каждой строки):
    users             — когда продавца видели первый и последний раз
    dialog_messages   — история диалога, «последние сообщения»
    analyses          — история анализов карточек
    products          — товары, за которыми продавец следит
    product_changes   — универсальный журнал изменений товара
                        (отсюда же «история цены» и «история фото» —
                        это просто фильтр по полю field)
    recommendations   — рекомендации + статус (ожидает/выполнено)
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import aiosqlite

from backend.memory.models import (
    AnalysisRecord,
    DialogMessage,
    ProductChange,
    ProductRecord,
    Recommendation,
)

log = logging.getLogger("selleros.memory")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS dialog_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dialog_user ON dialog_messages(user_id, created_at);

CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    article INTEGER NOT NULL,
    marketplace TEXT NOT NULL DEFAULT 'wildberries',
    title TEXT,
    price INTEGER,
    score INTEGER NOT NULL,
    verdict TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analyses_user ON analyses(user_id, created_at);

CREATE TABLE IF NOT EXISTS products (
    user_id INTEGER NOT NULL,
    article INTEGER NOT NULL,
    marketplace TEXT NOT NULL DEFAULT 'wildberries',
    title TEXT,
    price INTEGER,
    rating REAL,
    score INTEGER,
    photos INTEGER,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    PRIMARY KEY (user_id, article, marketplace)
);

CREATE TABLE IF NOT EXISTS product_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    article INTEGER NOT NULL,
    field TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    changed_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_changes_lookup
    ON product_changes(user_id, article, field, changed_at);

CREATE TABLE IF NOT EXISTS recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    article INTEGER NOT NULL,
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL,
    completed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_recs_user ON recommendations(user_id, article);
"""

#: Колонки products, добавленные под SellerData (данные продавца).
#: Ключ — имя колонки, значение — SQL-тип для ALTER TABLE ADD COLUMN.
#:
#: Таблица products уже могла существовать на диске у пользователей ДО
#: этого изменения (CREATE TABLE IF NOT EXISTS её не тронет), поэтому
#: недостающие колонки добавляются миграцией в connect() — см.
#: _migrate_products_columns(). Ничего не удаляется и не пересоздаётся.
PRODUCTS_SELLER_COLUMNS: dict[str, str] = {
    "feedbacks": "INTEGER",
    "price_source": "TEXT",
    "rating_source": "TEXT",
    "feedbacks_source": "TEXT",
    "sales": "INTEGER",
    "orders": "INTEGER",
    "period": "TEXT",
    "seller_updated_at": "REAL",
}


class MemoryStore:
    """
    Долговременная память. Использовать так:

        store = MemoryStore("data/argus_memory.db")
        await store.connect()
        ...
        await store.close()
    """

    #: Какие поля товара отслеживаем на изменения. Порядок важен —
    #: должен совпадать с порядком колонок в SELECT внутри upsert_product.
    TRACKED_FIELDS = ("price", "rating", "score", "photos")

    def __init__(self, db_path: str = "data/argus_memory.db"):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    # ------------------------------------------------------------- lifecycle

    async def connect(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self._db = await aiosqlite.connect(self.db_path)

        # WAL — чтение и запись не блокируют друг друга.
        # Для бота, где одни хендлеры читают, а другие пишут
        # одновременно, это важно даже на SQLite.
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(SCHEMA)
        await self._db.commit()

        await self._migrate_products_columns()

        log.info("Долговременная память ARGUS подключена: %s", self.db_path)

    async def _migrate_products_columns(self) -> None:
        """
        Идемпотентная миграция: добавляет в products колонки SellerData,
        которых ещё нет. Ничего не удаляет, таблицу не пересоздаёт —
        безопасно вызывать при каждом старте бота.
        """
        cursor = await self._db.execute("PRAGMA table_info(products)")
        rows = await cursor.fetchall()
        existing = {row[1] for row in rows}  # row[1] — имя колонки

        missing = {
            name: sql_type
            for name, sql_type in PRODUCTS_SELLER_COLUMNS.items()
            if name not in existing
        }

        if not missing:
            return

        for name, sql_type in missing.items():
            await self._db.execute(f"ALTER TABLE products ADD COLUMN {name} {sql_type}")

        await self._db.commit()

        log.info("Миграция products: добавлены колонки %s", ", ".join(missing))

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError(
                "MemoryStore не подключён — сначала вызовите await store.connect()"
            )
        return self._db

    # ------------------------------------------------------------------ users

    async def touch_user(self, user_id: int) -> None:
        """Отметить, что видели продавца (создаст запись при первом визите)."""
        now = time.time()
        await self.db.execute(
            """
            INSERT INTO users (id, first_seen, last_seen) VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET last_seen = excluded.last_seen
            """,
            (user_id, now, now),
        )
        await self.db.commit()

    # -------------------------------------------------------------- диалог

    async def add_message(self, user_id: int, role: str, content: str) -> None:
        await self.db.execute(
            "INSERT INTO dialog_messages (user_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, role, content, time.time()),
        )
        await self.db.commit()

    async def last_messages(self, user_id: int, limit: int = 20) -> list[DialogMessage]:
        """Последние сообщения в порядке от старых к новым (как в разговоре)."""
        cursor = await self.db.execute(
            "SELECT id, user_id, role, content, created_at FROM dialog_messages "
            "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        )
        rows = await cursor.fetchall()
        return [DialogMessage(*row) for row in reversed(rows)]

    # ------------------------------------------------------------- анализы

    async def add_analysis(
        self,
        user_id: int,
        article: int,
        marketplace: str,
        title: str,
        price: int | None,
        score: int,
        verdict: str,
    ) -> None:
        await self.db.execute(
            "INSERT INTO analyses "
            "(user_id, article, marketplace, title, price, score, verdict, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, article, marketplace, title, price, score, verdict, time.time()),
        )
        await self.db.commit()

    async def list_analyses(self, user_id: int, limit: int = 10) -> list[AnalysisRecord]:
        cursor = await self.db.execute(
            "SELECT id, user_id, article, marketplace, title, price, score, verdict, created_at "
            "FROM analyses WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        )
        rows = await cursor.fetchall()
        return [AnalysisRecord(*row) for row in rows]

    async def analyses_since(self, user_id: int, since_ts: float) -> list[AnalysisRecord]:
        cursor = await self.db.execute(
            "SELECT id, user_id, article, marketplace, title, price, score, verdict, created_at "
            "FROM analyses WHERE user_id = ? AND created_at >= ? ORDER BY created_at DESC",
            (user_id, since_ts),
        )
        rows = await cursor.fetchall()
        return [AnalysisRecord(*row) for row in rows]

    # ------------------------------------------------ товары + журнал изменений

    async def upsert_product(
        self,
        user_id: int,
        article: int,
        marketplace: str,
        title: str,
        price: int | None,
        rating: float | None,
        score: int | None,
        photos: int,
    ) -> None:
        """
        Обновить карточку товара в памяти продавца.

        Если товар уже был известен и что-то поменялось (цена, рейтинг,
        Score, число фото) — само запишет разницу в product_changes.
        Ничего дополнительно вызывать не нужно, это и есть автоматическая
        «история изменений товара».
        """
        cursor = await self.db.execute(
            "SELECT price, rating, score, photos FROM products "
            "WHERE user_id = ? AND article = ? AND marketplace = ?",
            (user_id, article, marketplace),
        )
        previous = await cursor.fetchone()
        now = time.time()

        new_values = {"price": price, "rating": rating, "score": score, "photos": photos}

        if previous is None:
            await self.db.execute(
                "INSERT INTO products "
                "(user_id, article, marketplace, title, price, rating, score, photos, "
                " first_seen, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, article, marketplace, title, price, rating, score, photos, now, now),
            )
        else:
            old_values = dict(zip(self.TRACKED_FIELDS, previous))

            for field in self.TRACKED_FIELDS:
                if old_values[field] != new_values[field]:
                    await self.db.execute(
                        "INSERT INTO product_changes "
                        "(user_id, article, field, old_value, new_value, changed_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            user_id, article, field,
                            _to_text(old_values[field]),
                            _to_text(new_values[field]),
                            now,
                        ),
                    )

            await self.db.execute(
                "UPDATE products SET title=?, price=?, rating=?, score=?, photos=?, last_seen=? "
                "WHERE user_id=? AND article=? AND marketplace=?",
                (title, price, rating, score, photos, now, user_id, article, marketplace),
            )

        await self.db.commit()

    #: Общий список колонок products в порядке полей ProductRecord —
    #: используется во всех SELECT, которые строят ProductRecord(*row).
    _PRODUCT_COLUMNS = (
        "user_id, article, marketplace, title, price, rating, score, photos, "
        "first_seen, last_seen, feedbacks, price_source, rating_source, "
        "feedbacks_source, sales, orders, period, seller_updated_at"
    )

    async def list_products(self, user_id: int) -> list[ProductRecord]:
        cursor = await self.db.execute(
            f"SELECT {self._PRODUCT_COLUMNS} FROM products "
            "WHERE user_id = ? ORDER BY last_seen DESC",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [ProductRecord(*row) for row in rows]

    async def get_last_known_snapshot(
        self,
        article: int,
        marketplace: str = "wildberries",
    ) -> ProductRecord | None:
        """
        Последний известный снимок товара — ЛЮБОГО пользователя, кто его
        разбирал, не только текущего.

        Нужен для WB Engine (HistoryFallbackSource): сам товар и его
        цена/рейтинг не принадлежат конкретному продавцу — если ARGUS
        уже видел эту карточку хоть у кого-то, отдать её как резерв
        честнее, чем возвращать пустоту.
        """
        cursor = await self.db.execute(
            f"SELECT {self._PRODUCT_COLUMNS} FROM products "
            "WHERE article = ? AND marketplace = ? "
            "ORDER BY last_seen DESC LIMIT 1",
            (article, marketplace),
        )
        row = await cursor.fetchone()
        return ProductRecord(*row) if row else None

    async def get_product(
        self,
        user_id: int,
        article: int,
        marketplace: str = "wildberries",
    ) -> ProductRecord | None:
        """Снимок ОДНОГО товара конкретного продавца (для экрана «Точный анализ»)."""
        cursor = await self.db.execute(
            f"SELECT {self._PRODUCT_COLUMNS} FROM products "
            "WHERE user_id = ? AND article = ? AND marketplace = ?",
            (user_id, article, marketplace),
        )
        row = await cursor.fetchone()
        return ProductRecord(*row) if row else None

    async def save_seller_data(
        self,
        user_id: int,
        article: int,
        marketplace: str = "wildberries",
        *,
        price: float | None = None,
        rating: float | None = None,
        feedbacks: int | None = None,
        sales: int | None = None,
        orders: int | None = None,
        period: str | None = None,
        price_source: str | None = None,
        rating_source: str | None = None,
        feedbacks_source: str | None = None,
        updated_at: float | None = None,
    ) -> bool:
        """
        Обновить ТОЛЬКО seller-поля существующего товара (SellerData).

        Не трогает title/photos/score и не делает upsert: строка товара
        должна уже существовать (её создаёт upsert_product() в момент
        добавления товара). Если строки ещё нет — это значит, что товар
        не был сохранён через обычный сценарий добавления, и мы честно
        логируем это, вместо того чтобы вставлять запись с пустыми
        полями карточки.

        Возвращает True, если строка была обновлена, False — если
        товар не найден.
        """
        cursor = await self.db.execute(
            "SELECT 1 FROM products WHERE user_id = ? AND article = ? AND marketplace = ?",
            (user_id, article, marketplace),
        )
        if await cursor.fetchone() is None:
            log.warning(
                "save_seller_data: товар %s (user %s) не найден в products — "
                "сначала должен быть вызван upsert_product()",
                article, user_id,
            )
            return False

        await self.db.execute(
            "UPDATE products SET "
            "price=?, rating=?, feedbacks=?, sales=?, orders=?, period=?, "
            "price_source=?, rating_source=?, feedbacks_source=?, seller_updated_at=? "
            "WHERE user_id=? AND article=? AND marketplace=?",
            (
                price, rating, feedbacks, sales, orders, period,
                price_source, rating_source, feedbacks_source,
                updated_at if updated_at is not None else time.time(),
                user_id, article, marketplace,
            ),
        )
        await self.db.commit()
        return True

    async def delete_product(
        self,
        user_id: int,
        article: int,
        marketplace: str = "wildberries",
    ) -> bool:
        """
        Удалить товар из «Мои товары» + данные, которые принадлежат ИМЕННО
        этой паре (продавец, артикул): журнал изменений (product_changes)
        и рекомендации (recommendations) по этому товару.

        НЕ трогает analyses («История анализов» / «Отчёты») — это отдельный
        самостоятельный таймлайн разборов, он не должен пропадать только
        из-за того, что продавец перестал отслеживать товар.

        Строго скоуплено по user_id — данные других продавцов и другие
        товары этого же продавца не задеваются.

        Возвращает True, если строка в products была найдена и удалена;
        False — если товара с таким user_id/article/marketplace не было
        (например, уже удалили раньше).
        """
        cursor = await self.db.execute(
            "DELETE FROM products WHERE user_id = ? AND article = ? AND marketplace = ?",
            (user_id, article, marketplace),
        )
        deleted = cursor.rowcount > 0

        await self.db.execute(
            "DELETE FROM product_changes WHERE user_id = ? AND article = ?",
            (user_id, article),
        )
        await self.db.execute(
            "DELETE FROM recommendations WHERE user_id = ? AND article = ?",
            (user_id, article),
        )

        await self.db.commit()

        if deleted:
            log.info("Товар %s удалён из памяти продавца %s", article, user_id)

        return deleted

    async def changes_for(
        self,
        user_id: int,
        article: int,
        field: str | None = None,
        limit: int = 20,
    ) -> list[ProductChange]:
        query = (
            "SELECT id, user_id, article, field, old_value, new_value, changed_at "
            "FROM product_changes WHERE user_id = ? AND article = ?"
        )
        params: list = [user_id, article]

        if field is not None:
            query += " AND field = ?"
            params.append(field)

        query += " ORDER BY changed_at DESC LIMIT ?"
        params.append(limit)

        cursor = await self.db.execute(query, params)
        rows = await cursor.fetchall()
        return [ProductChange(*row) for row in rows]

    async def price_history(self, user_id: int, article: int) -> list[ProductChange]:
        """История изменения цены — удобная обёртка над changes_for."""
        return await self.changes_for(user_id, article, field="price")

    async def photo_history(self, user_id: int, article: int) -> list[ProductChange]:
        """История изменения количества фото — удобная обёртка над changes_for."""
        return await self.changes_for(user_id, article, field="photos")

    # ------------------------------------------------------- рекомендации

    async def add_recommendations(self, user_id: int, article: int, texts: list[str]) -> None:
        if not texts:
            return

        now = time.time()
        await self.db.executemany(
            "INSERT INTO recommendations (user_id, article, text, status, created_at) "
            "VALUES (?, ?, ?, 'pending', ?)",
            [(user_id, article, text, now) for text in texts],
        )
        await self.db.commit()

    async def list_recommendations(
        self,
        user_id: int,
        article: int | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> list[Recommendation]:
        query = (
            "SELECT id, user_id, article, text, status, created_at, completed_at "
            "FROM recommendations WHERE user_id = ?"
        )
        params: list = [user_id]

        if article is not None:
            query += " AND article = ?"
            params.append(article)

        if status is not None:
            query += " AND status = ?"
            params.append(status)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        cursor = await self.db.execute(query, params)
        rows = await cursor.fetchall()
        return [Recommendation(*row) for row in rows]

    async def complete_recommendation(self, recommendation_id: int) -> None:
        """
        Отметить рекомендацию выполненной.

        Сюда пока никто не обращается — кнопки «✅ Сделано» появятся
        в личном кабинете (следующий этап). Метод уже готов её принять.
        """
        await self.db.execute(
            "UPDATE recommendations SET status='done', completed_at=? WHERE id=?",
            (time.time(), recommendation_id),
        )
        await self.db.commit()


def _to_text(value) -> str | None:
    return None if value is None else str(value)
