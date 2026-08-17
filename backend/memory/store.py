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
    ProductDecision,
    ProductMetricSnapshot,
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

CREATE TABLE IF NOT EXISTS conversation_summaries (
    user_id INTEGER NOT NULL,
    article INTEGER NOT NULL DEFAULT 0,
    summary TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (user_id, article)
);

CREATE TABLE IF NOT EXISTS product_conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    article INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_prod_conv
    ON product_conversations(user_id, article, created_at);

-- Telegram Mini App auth sessions (token hash only; never plaintext).
CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash TEXT PRIMARY KEY,
    seller_id TEXT NOT NULL,
    telegram_user_id TEXT NOT NULL,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    revoked_at REAL,
    last_seen_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_seller
    ON auth_sessions(seller_id);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_expires
    ON auth_sessions(expires_at);

CREATE TABLE IF NOT EXISTS product_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    article INTEGER NOT NULL,
    topic TEXT NOT NULL,
    problem TEXT NOT NULL DEFAULT '',
    evidence TEXT NOT NULL DEFAULT '',
    recommendation TEXT NOT NULL DEFAULT '',
    seller_question TEXT NOT NULL DEFAULT '',
    solution_options TEXT NOT NULL DEFAULT '',
    seller_choice TEXT,
    action TEXT,
    outcome TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    problem_id TEXT,
    selected_solution_id TEXT,
    seller_comment TEXT,
    status TEXT NOT NULL DEFAULT 'PROPOSED',
    outcome_tracker_id TEXT,
    UNIQUE(user_id, article, topic)
);
CREATE INDEX IF NOT EXISTS idx_product_decisions
    ON product_decisions(user_id, article, topic);

-- Dynamic Analytics: time-series of seller/card metrics per article.
-- Never mix articles / periods / sources in one row.
CREATE TABLE IF NOT EXISTS product_metric_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    article INTEGER NOT NULL,
    marketplace TEXT NOT NULL DEFAULT 'wildberries',
    captured_at REAL NOT NULL,
    period TEXT,
    price REAL,
    rating REAL,
    feedbacks INTEGER,
    impressions INTEGER,
    views INTEGER,
    clicks INTEGER,
    ctr REAL,
    orders INTEGER,
    sales INTEGER,
    cvr REAL,
    revenue REAL,
    costs REAL,
    profit REAL,
    margin REAL,
    stock INTEGER,
    ad_spend REAL,
    cost REAL,
    returns INTEGER,
    source TEXT,
    confidence REAL,
    provenance TEXT
);
CREATE INDEX IF NOT EXISTS idx_metric_snaps
    ON product_metric_snapshots(user_id, article, marketplace, captured_at);

-- Foundation: structured seller actions (ActionService).
-- Links baseline snapshot + check_after + optional outcome_id.
CREATE TABLE IF NOT EXISTS seller_actions (
    action_id TEXT PRIMARY KEY,
    seller_id INTEGER NOT NULL,
    article INTEGER NOT NULL,
    marketplace TEXT NOT NULL DEFAULT 'wildberries',
    action_type TEXT NOT NULL,
    recommendation TEXT NOT NULL,
    status TEXT NOT NULL,
    accepted_at REAL,
    executed_at REAL,
    baseline_snapshot_id INTEGER,
    expected_effect TEXT,
    check_after REAL,
    reminder_at REAL,
    outcome_id TEXT,
    diagnosis TEXT,
    metadata TEXT
);
CREATE INDEX IF NOT EXISTS idx_seller_actions_product
    ON seller_actions(seller_id, article, status);
CREATE INDEX IF NOT EXISTS idx_seller_actions_due
    ON seller_actions(status, check_after);
"""

#: Decision Memory v1 columns (idempotent ALTER ADD).
PRODUCT_DECISIONS_V1_COLUMNS: dict[str, str] = {
    "problem_id": "TEXT",
    "selected_solution_id": "TEXT",
    "seller_comment": "TEXT",
    "status": "TEXT NOT NULL DEFAULT 'PROPOSED'",
    "outcome_tracker_id": "TEXT",
}

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
    # Private metrics (optional)
    "ctr": "REAL",
    "cvr": "REAL",
    "returns": "INTEGER",
    "ad_spend": "REAL",
    "cost": "REAL",
    "commission": "REAL",
    "logistics": "REAL",
    "storage": "REAL",
    "impressions": "INTEGER",
    "views": "INTEGER",
    # WB card group ids (reviews endpoint); миграция ALTER ADD COLUMN.
    "imt_id": "INTEGER",
    "root_id": "INTEGER",
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
        await self._migrate_product_decisions_columns()
        # conversation_summaries / product_conversations — в SCHEMA
        # (CREATE TABLE IF NOT EXISTS), отдельная миграция не нужна.

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

    async def _migrate_product_decisions_columns(self) -> None:
        """Idempotent: add Decision Memory v1 columns to product_decisions."""
        cursor = await self._db.execute("PRAGMA table_info(product_decisions)")
        rows = await cursor.fetchall()
        existing = {row[1] for row in rows}
        missing = {
            name: sql_type
            for name, sql_type in PRODUCT_DECISIONS_V1_COLUMNS.items()
            if name not in existing
        }
        if not missing:
            return
        for name, sql_type in missing.items():
            await self._db.execute(
                f"ALTER TABLE product_decisions ADD COLUMN {name} {sql_type}"
            )
        await self._db.commit()
        log.info(
            "Миграция product_decisions: добавлены колонки %s",
            ", ".join(missing),
        )

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

    async def get_last_seen(self, user_id: int) -> float | None:
        """Когда продавца видели в Telegram / Mini App. None — ещё не было визита."""
        cursor = await self.db.execute(
            "SELECT last_seen FROM users WHERE id = ?",
            (int(user_id),),
        )
        row = await cursor.fetchone()
        return float(row[0]) if row else None

    async def list_user_ids(self, limit: int = 5000) -> list[int]:
        cursor = await self.db.execute(
            "SELECT id FROM users ORDER BY last_seen DESC LIMIT ?",
            (int(limit),),
        )
        rows = await cursor.fetchall()
        return [int(row[0]) for row in rows]

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

    # -------------------------------------------- product conversation + summary

    async def add_product_message(
        self,
        user_id: int,
        article: int,
        role: str,
        content: str,
    ) -> None:
        """Сообщение discussion, привязанное к артикулу (seller-scoped)."""
        await self.db.execute(
            "INSERT INTO product_conversations "
            "(user_id, article, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, article, role, content, time.time()),
        )
        await self.db.commit()

    async def last_product_messages(
        self,
        user_id: int,
        article: int,
        limit: int = 40,
    ) -> list[DialogMessage]:
        cursor = await self.db.execute(
            "SELECT id, user_id, role, content, created_at FROM product_conversations "
            "WHERE user_id = ? AND article = ? ORDER BY id DESC LIMIT ?",
            (user_id, article, limit),
        )
        rows = await cursor.fetchall()
        return [DialogMessage(*row) for row in reversed(rows)]

    async def get_conversation_summary(
        self,
        user_id: int,
        article: int = 0,
    ) -> str | None:
        cursor = await self.db.execute(
            "SELECT summary FROM conversation_summaries "
            "WHERE user_id = ? AND article = ?",
            (user_id, article),
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def save_conversation_summary(
        self,
        user_id: int,
        article: int,
        summary: str,
    ) -> None:
        await self.db.execute(
            "INSERT INTO conversation_summaries (user_id, article, summary, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, article) DO UPDATE SET "
            "summary = excluded.summary, updated_at = excluded.updated_at",
            (user_id, article, summary, time.time()),
        )
        await self.db.commit()

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
        imt_id: int | None = None,
        root_id: int | None = None,
    ) -> None:
        """
        Обновить карточку товара в памяти продавца.

        Если товар уже был известен и что-то поменялось (цена, рейтинг,
        Score, число фото) — само запишет разницу в product_changes.
        Ничего дополнительно вызывать не нужно, это и есть автоматическая
        «история изменений товара».

        imt_id/root_id: None от WB не затирает уже сохранённые значения.
        """
        cursor = await self.db.execute(
            "SELECT price, rating, score, photos, price_source, rating_source, "
            "imt_id, root_id "
            "FROM products "
            "WHERE user_id = ? AND article = ? AND marketplace = ?",
            (user_id, article, marketplace),
        )
        previous = await cursor.fetchone()
        now = time.time()

        # Синхронизация якорей reviews API
        if imt_id is None and root_id is not None:
            imt_id = root_id
        if root_id is None and imt_id is not None:
            root_id = imt_id

        if previous is None:
            await self.db.execute(
                "INSERT INTO products "
                "(user_id, article, marketplace, title, price, rating, score, photos, "
                " first_seen, last_seen, imt_id, root_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id, article, marketplace, title, price, rating, score, photos,
                    now, now, imt_id, root_id,
                ),
            )
        else:
            (
                old_price, old_rating, old_score, old_photos,
                price_source, rating_source, old_imt, old_root,
            ) = previous

            # Правило: new is None → preserve; seller-entered > WB unknown.
            if price_source in ("user", "api") or price is None:
                price = old_price
            if rating_source in ("user", "api") or rating is None:
                rating = old_rating
            if score is None:
                score = old_score
            if imt_id is None:
                imt_id = old_imt
            if root_id is None:
                root_id = old_root
            if imt_id is None and root_id is not None:
                imt_id = root_id
            if root_id is None and imt_id is not None:
                root_id = imt_id

            new_values = {"price": price, "rating": rating, "score": score, "photos": photos}
            old_values = {
                "price": old_price,
                "rating": old_rating,
                "score": old_score,
                "photos": old_photos,
            }

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
                "UPDATE products SET title=?, price=?, rating=?, score=?, photos=?, "
                "last_seen=?, imt_id=?, root_id=? "
                "WHERE user_id=? AND article=? AND marketplace=?",
                (
                    title, price, rating, score, photos, now, imt_id, root_id,
                    user_id, article, marketplace,
                ),
            )

        await self.db.commit()

    #: Общий список колонок products в порядке полей ProductRecord —
    #: используется во всех SELECT, которые строят ProductRecord(*row).
    _PRODUCT_COLUMNS = (
        "user_id, article, marketplace, title, price, rating, score, photos, "
        "first_seen, last_seen, feedbacks, price_source, rating_source, "
        "feedbacks_source, sales, orders, period, seller_updated_at, "
        "ctr, cvr, returns, ad_spend, cost, commission, logistics, storage, "
        "impressions, views, "
        "imt_id, root_id"
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
        ctr: float | None = None,
        cvr: float | None = None,
        returns: int | None = None,
        ad_spend: float | None = None,
        cost: float | None = None,
        commission: float | None = None,
        logistics: float | None = None,
        storage: float | None = None,
        impressions: int | None = None,
        views: int | None = None,
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

        None по коммерции/метрикам не затирает уже сохранённое (COALESCE).

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
            "price=COALESCE(?, price), rating=COALESCE(?, rating), "
            "feedbacks=COALESCE(?, feedbacks), sales=COALESCE(?, sales), "
            "orders=COALESCE(?, orders), period=COALESCE(?, period), "
            "ctr=COALESCE(?, ctr), cvr=COALESCE(?, cvr), "
            "returns=COALESCE(?, returns), ad_spend=COALESCE(?, ad_spend), "
            "cost=COALESCE(?, cost), commission=COALESCE(?, commission), "
            "logistics=COALESCE(?, logistics), storage=COALESCE(?, storage), "
            "impressions=COALESCE(?, impressions), views=COALESCE(?, views), "
            "price_source=COALESCE(?, price_source), "
            "rating_source=COALESCE(?, rating_source), "
            "feedbacks_source=COALESCE(?, feedbacks_source), "
            "seller_updated_at=? "
            "WHERE user_id=? AND article=? AND marketplace=?",
            (
                price, rating, feedbacks, sales, orders, period,
                ctr, cvr, returns, ad_spend, cost, commission, logistics, storage,
                impressions, views,
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

    # ------------------------------------------------ product decisions (v2 / Decision Memory v1)

    _DECISION_COLUMNS = (
        "id, user_id, article, topic, problem, evidence, recommendation, "
        "seller_question, solution_options, seller_choice, action, outcome, "
        "created_at, updated_at, problem_id, selected_solution_id, "
        "seller_comment, status, outcome_tracker_id"
    )

    def _row_to_product_decision(self, row) -> ProductDecision:
        # Older DBs may return fewer columns before migration settles — pad.
        vals = list(row)
        while len(vals) < 19:
            vals.append(None)
        (
            _id, user_id, article, topic, problem, evidence, recommendation,
            seller_question, solution_options, seller_choice, action, outcome,
            created_at, updated_at, problem_id, selected_solution_id,
            seller_comment, status, outcome_tracker_id,
        ) = vals[:19]
        return ProductDecision(
            id=_id,
            user_id=user_id,
            article=article,
            topic=topic,
            problem=problem or "",
            evidence=evidence or "",
            recommendation=recommendation or "",
            seller_question=seller_question or "",
            solution_options=solution_options or "",
            seller_choice=seller_choice,
            action=action,
            outcome=outcome,
            created_at=created_at or 0.0,
            updated_at=updated_at or 0.0,
            problem_id=problem_id,
            selected_solution_id=selected_solution_id,
            seller_comment=seller_comment,
            status=status or "PROPOSED",
            outcome_tracker_id=outcome_tracker_id,
        )

    def _row_to_decision_record(self, row):
        from backend.intelligence.solution_research import DecisionRecord, DecisionStatus
        import json

        pd = self._row_to_product_decision(row)
        options: list = []
        raw = pd.solution_options or ""
        if raw.strip().startswith("["):
            try:
                options = json.loads(raw)
            except Exception:
                options = []
        evid = [x for x in (pd.evidence or "").split(",") if x.strip()]
        try:
            status = DecisionStatus(pd.status or "PROPOSED")
        except Exception:
            status = DecisionStatus.PROPOSED
        return DecisionRecord(
            id=pd.id,
            seller_id=pd.user_id,
            product_article=pd.article,
            topic=pd.topic,
            problem_id=pd.problem_id,
            evidence_ids=evid,
            recommendation=pd.recommendation,
            solution_options=options if isinstance(options, list) else [],
            selected_solution_id=pd.selected_solution_id,
            seller_comment=pd.seller_comment,
            status=status,
            problem=pd.problem,
            seller_question=pd.seller_question,
            action=pd.action,
            outcome=pd.outcome,
            outcome_tracker_id=pd.outcome_tracker_id,
            created_at=pd.created_at,
            updated_at=pd.updated_at,
        )

    async def upsert_product_decision(
        self,
        user_id: int,
        article: int,
        topic: str,
        *,
        problem: str = "",
        evidence: str = "",
        recommendation: str = "",
        seller_question: str = "",
        solution_options: str = "",
        seller_choice: str | None = None,
        action: str | None = None,
        outcome: str | None = None,
        problem_id: str | None = None,
        selected_solution_id: str | None = None,
        seller_comment: str | None = None,
        status: str | None = None,
        outcome_tracker_id: str | None = None,
    ) -> ProductDecision:
        """Seller-isolated decision memory (UNIQUE user/article/topic)."""
        now = time.time()
        topic_n = (topic or "решение").strip().lower()
        status_v = status or "PROPOSED"
        await self.db.execute(
            "INSERT INTO product_decisions "
            "(user_id, article, topic, problem, evidence, recommendation, "
            " seller_question, solution_options, seller_choice, action, outcome, "
            " created_at, updated_at, problem_id, selected_solution_id, "
            " seller_comment, status, outcome_tracker_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, article, topic) DO UPDATE SET "
            "problem = CASE WHEN excluded.problem != '' THEN excluded.problem "
            "               ELSE product_decisions.problem END, "
            "evidence = CASE WHEN excluded.evidence != '' THEN excluded.evidence "
            "                ELSE product_decisions.evidence END, "
            "recommendation = CASE WHEN excluded.recommendation != '' "
            "                      THEN excluded.recommendation "
            "                      ELSE product_decisions.recommendation END, "
            "seller_question = CASE WHEN excluded.seller_question != '' "
            "                       THEN excluded.seller_question "
            "                       ELSE product_decisions.seller_question END, "
            "solution_options = CASE WHEN excluded.solution_options != '' "
            "                        THEN excluded.solution_options "
            "                        ELSE product_decisions.solution_options END, "
            "seller_choice = COALESCE(excluded.seller_choice, "
            "                         product_decisions.seller_choice), "
            "action = COALESCE(excluded.action, product_decisions.action), "
            "outcome = COALESCE(excluded.outcome, product_decisions.outcome), "
            "problem_id = COALESCE(excluded.problem_id, product_decisions.problem_id), "
            "selected_solution_id = COALESCE(excluded.selected_solution_id, "
            "                         product_decisions.selected_solution_id), "
            "seller_comment = COALESCE(excluded.seller_comment, "
            "                          product_decisions.seller_comment), "
            "status = CASE WHEN excluded.status IS NOT NULL AND excluded.status != '' "
            "              THEN excluded.status ELSE product_decisions.status END, "
            "outcome_tracker_id = COALESCE(excluded.outcome_tracker_id, "
            "                              product_decisions.outcome_tracker_id), "
            "updated_at = excluded.updated_at",
            (
                user_id, int(article), topic_n,
                problem or "", evidence or "", recommendation or "",
                seller_question or "", solution_options or "",
                seller_choice, action, outcome, now, now,
                problem_id, selected_solution_id, seller_comment,
                status_v, outcome_tracker_id,
            ),
        )
        await self.db.commit()
        row = await self.get_product_decision(user_id, article, topic_n)
        assert row is not None
        return row

    async def get_product_decision(
        self,
        user_id: int,
        article: int,
        topic: str,
    ) -> ProductDecision | None:
        topic_n = (topic or "").strip().lower()
        cursor = await self.db.execute(
            f"SELECT {self._DECISION_COLUMNS} FROM product_decisions "
            "WHERE user_id = ? AND article = ? AND topic = ?",
            (user_id, int(article), topic_n),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_product_decision(row)

    async def upsert_decision_record(self, record) -> "object":
        """Persist DecisionRecord (seller_id + product_article isolation)."""
        import json
        from backend.intelligence.solution_research import DecisionStatus

        status = record.status
        if isinstance(status, DecisionStatus):
            status_s = status.value
        else:
            status_s = str(status or "PROPOSED")
        options_raw = record.solution_options
        if isinstance(options_raw, list):
            options_txt = json.dumps(options_raw, ensure_ascii=False)
        else:
            options_txt = options_raw or ""
        evid = record.evidence_ids or []
        evidence_txt = ",".join(str(x) for x in evid)
        await self.upsert_product_decision(
            int(record.seller_id),
            int(record.product_article),
            record.topic,
            problem=record.problem or "",
            evidence=evidence_txt,
            recommendation=record.recommendation or "",
            seller_question=record.seller_question or "",
            solution_options=options_txt,
            seller_choice=(
                (record.selected_option() or {}).get("title")
                if hasattr(record, "selected_option") and record.selected_solution_id
                else None
            ),
            action=record.action,
            outcome=record.outcome,
            problem_id=record.problem_id,
            selected_solution_id=record.selected_solution_id,
            seller_comment=record.seller_comment,
            status=status_s,
            outcome_tracker_id=record.outcome_tracker_id,
        )
        return await self.get_decision_record(
            int(record.seller_id),
            int(record.product_article),
            record.topic,
        )

    async def get_decision_record(
        self,
        seller_id: int,
        product_article: int,
        topic: str,
    ):
        topic_n = (topic or "").strip().lower()
        cursor = await self.db.execute(
            f"SELECT {self._DECISION_COLUMNS} FROM product_decisions "
            "WHERE user_id = ? AND article = ? AND topic = ?",
            (int(seller_id), int(product_article), topic_n),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_decision_record(row)

    async def set_product_decision_choice(
        self,
        user_id: int,
        article: int,
        topic: str,
        seller_choice: str,
        *,
        action: str | None = None,
        selected_solution_id: str | None = None,
        status: str = "SELECTED",
        seller_comment: str | None = None,
    ) -> ProductDecision | None:
        existing = await self.get_product_decision(user_id, article, topic)
        if existing is None:
            return await self.upsert_product_decision(
                user_id, article, topic,
                seller_choice=seller_choice,
                action=action,
                selected_solution_id=selected_solution_id,
                status=status,
                seller_comment=seller_comment,
            )
        return await self.upsert_product_decision(
            user_id, article, topic,
            problem=existing.problem,
            evidence=existing.evidence,
            recommendation=existing.recommendation,
            seller_question=existing.seller_question,
            solution_options=existing.solution_options,
            seller_choice=seller_choice,
            action=action or existing.action,
            outcome=existing.outcome,
            problem_id=existing.problem_id,
            selected_solution_id=selected_solution_id or existing.selected_solution_id,
            seller_comment=seller_comment if seller_comment is not None else existing.seller_comment,
            status=status or existing.status,
            outcome_tracker_id=existing.outcome_tracker_id,
        )

    async def set_decision_status(
        self,
        seller_id: int,
        product_article: int,
        topic: str,
        status: str,
        *,
        seller_comment: str | None = None,
        outcome_tracker_id: str | None = None,
    ):
        existing = await self.get_product_decision(seller_id, product_article, topic)
        if existing is None:
            return None
        return await self.upsert_product_decision(
            seller_id, product_article, topic,
            problem=existing.problem,
            evidence=existing.evidence,
            recommendation=existing.recommendation,
            seller_question=existing.seller_question,
            solution_options=existing.solution_options,
            seller_choice=existing.seller_choice,
            action=existing.action,
            outcome=existing.outcome,
            problem_id=existing.problem_id,
            selected_solution_id=existing.selected_solution_id,
            seller_comment=seller_comment if seller_comment is not None else existing.seller_comment,
            status=status,
            outcome_tracker_id=outcome_tracker_id or existing.outcome_tracker_id,
        )

    # --------------------------------------------------------- metric snapshots (Dynamic Analytics)

    _METRIC_SNAP_COLUMNS = (
        "id, user_id, article, marketplace, captured_at, period, "
        "price, rating, feedbacks, impressions, views, clicks, ctr, "
        "orders, sales, cvr, revenue, costs, profit, margin, stock, "
        "ad_spend, cost, returns, source, confidence, provenance"
    )

    async def save_metric_snapshot(
        self,
        user_id: int,
        article: int,
        marketplace: str = "wildberries",
        *,
        captured_at: float | None = None,
        period: str | None = None,
        price: float | None = None,
        rating: float | None = None,
        feedbacks: int | None = None,
        impressions: int | None = None,
        views: int | None = None,
        clicks: int | None = None,
        ctr: float | None = None,
        orders: int | None = None,
        sales: int | None = None,
        cvr: float | None = None,
        revenue: float | None = None,
        costs: float | None = None,
        profit: float | None = None,
        margin: float | None = None,
        stock: int | None = None,
        ad_spend: float | None = None,
        cost: float | None = None,
        returns: int | None = None,
        source: str | None = None,
        confidence: float | None = None,
        provenance: str | dict | None = None,
        min_interval_sec: float = 3600.0,
    ) -> int | None:
        """
        Persist a metric snapshot. Dedupes if the latest snap for the same
        (user, article, marketplace, period) is within min_interval_sec and
        values are identical — returns existing id without insert.

        Returns new/existing snapshot id, or None if nothing useful to store.
        """
        import json as _json

        has_any = any(
            v is not None
            for v in (
                price, rating, feedbacks, impressions, views, clicks, ctr,
                orders, sales, cvr, revenue, costs, profit, margin, stock,
                ad_spend, cost, returns,
            )
        )
        if not has_any:
            return None

        ts = float(captured_at if captured_at is not None else time.time())
        prov_text: str | None
        if isinstance(provenance, dict):
            prov_text = _json.dumps(provenance, ensure_ascii=False)
        elif isinstance(provenance, str):
            prov_text = provenance
        else:
            prov_text = None

        # Dedupe: same period + near-identical within interval
        cursor = await self.db.execute(
            f"SELECT {self._METRIC_SNAP_COLUMNS} FROM product_metric_snapshots "
            "WHERE user_id=? AND article=? AND marketplace=? "
            "AND IFNULL(period,'')=IFNULL(?, '') "
            "ORDER BY captured_at DESC LIMIT 1",
            (user_id, article, marketplace, period),
        )
        row = await cursor.fetchone()
        if row is not None:
            prev = ProductMetricSnapshot(*row)
            if (ts - float(prev.captured_at)) < float(min_interval_sec):
                same = (
                    prev.price == price and prev.rating == rating
                    and prev.feedbacks == feedbacks
                    and prev.impressions == impressions and prev.views == views
                    and prev.clicks == clicks and prev.ctr == ctr
                    and prev.orders == orders and prev.sales == sales
                    and prev.cvr == cvr and prev.revenue == revenue
                    and prev.costs == costs and prev.profit == profit
                    and prev.margin == margin and prev.stock == stock
                    and prev.ad_spend == ad_spend and prev.cost == cost
                    and prev.returns == returns
                )
                if same:
                    return prev.id

        cursor = await self.db.execute(
            "INSERT INTO product_metric_snapshots ("
            "user_id, article, marketplace, captured_at, period, "
            "price, rating, feedbacks, impressions, views, clicks, ctr, "
            "orders, sales, cvr, revenue, costs, profit, margin, stock, "
            "ad_spend, cost, returns, source, confidence, provenance"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                user_id, article, marketplace, ts, period,
                price, rating, feedbacks, impressions, views, clicks, ctr,
                orders, sales, cvr, revenue, costs, profit, margin, stock,
                ad_spend, cost, returns, source, confidence, prov_text,
            ),
        )
        await self.db.commit()
        return int(cursor.lastrowid) if cursor.lastrowid else None

    async def list_metric_snapshots(
        self,
        user_id: int,
        article: int,
        marketplace: str = "wildberries",
        *,
        since_ts: float | None = None,
        until_ts: float | None = None,
        period: str | None = None,
        limit: int = 60,
    ) -> list[ProductMetricSnapshot]:
        """Chronological (oldest→newest) snapshots for one article. Never mixes articles."""
        clauses = ["user_id=?", "article=?", "marketplace=?"]
        args: list = [user_id, article, marketplace]
        if since_ts is not None:
            clauses.append("captured_at>=?")
            args.append(float(since_ts))
        if until_ts is not None:
            clauses.append("captured_at<=?")
            args.append(float(until_ts))
        if period is not None:
            clauses.append("IFNULL(period,'')=IFNULL(?, '')")
            args.append(period)
        args.append(int(limit))
        cursor = await self.db.execute(
            f"SELECT {self._METRIC_SNAP_COLUMNS} FROM product_metric_snapshots "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY captured_at ASC LIMIT ?",
            tuple(args),
        )
        rows = await cursor.fetchall()
        return [ProductMetricSnapshot(*r) for r in rows]

    async def count_metric_snapshots(
        self,
        user_id: int,
        article: int,
        marketplace: str = "wildberries",
    ) -> int:
        cursor = await self.db.execute(
            "SELECT COUNT(*) FROM product_metric_snapshots "
            "WHERE user_id=? AND article=? AND marketplace=?",
            (user_id, article, marketplace),
        )
        row = await cursor.fetchone()
        return int(row[0]) if row else 0

    # ── Foundation: seller_actions ────────────────────────────────────────

    async def save_seller_action(self, payload: dict) -> None:
        import json
        meta = payload.get("metadata") or {}
        if not isinstance(meta, str):
            meta = json.dumps(meta, ensure_ascii=False)
        await self.db.execute(
            "INSERT INTO seller_actions ("
            "action_id, seller_id, article, marketplace, action_type, recommendation, "
            "status, accepted_at, executed_at, baseline_snapshot_id, expected_effect, "
            "check_after, reminder_at, outcome_id, diagnosis, metadata"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(action_id) DO UPDATE SET "
            "status=excluded.status, accepted_at=excluded.accepted_at, "
            "executed_at=excluded.executed_at, baseline_snapshot_id=excluded.baseline_snapshot_id, "
            "expected_effect=excluded.expected_effect, check_after=excluded.check_after, "
            "reminder_at=excluded.reminder_at, outcome_id=excluded.outcome_id, "
            "diagnosis=excluded.diagnosis, metadata=excluded.metadata, "
            "recommendation=excluded.recommendation, action_type=excluded.action_type",
            (
                payload["action_id"],
                int(payload["seller_id"]),
                int(payload["article"]),
                payload.get("marketplace") or "wildberries",
                payload.get("action_type"),
                payload.get("recommendation") or "",
                payload.get("status"),
                payload.get("accepted_at"),
                payload.get("executed_at"),
                payload.get("baseline_snapshot_id"),
                payload.get("expected_effect"),
                payload.get("check_after"),
                payload.get("reminder_at"),
                payload.get("outcome_id"),
                payload.get("diagnosis"),
                meta,
            ),
        )
        await self.db.commit()

    async def get_seller_action(self, action_id: str) -> dict | None:
        import json
        cur = await self.db.execute(
            "SELECT action_id, seller_id, article, marketplace, action_type, recommendation, "
            "status, accepted_at, executed_at, baseline_snapshot_id, expected_effect, "
            "check_after, reminder_at, outcome_id, diagnosis, metadata "
            "FROM seller_actions WHERE action_id=?",
            (action_id,),
        )
        row = await cur.fetchone()
        if not row:
            return None
        meta = row[15]
        if isinstance(meta, str) and meta:
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        return {
            "action_id": row[0],
            "seller_id": row[1],
            "article": row[2],
            "marketplace": row[3],
            "action_type": row[4],
            "recommendation": row[5],
            "status": row[6],
            "accepted_at": row[7],
            "executed_at": row[8],
            "baseline_snapshot_id": row[9],
            "expected_effect": row[10],
            "check_after": row[11],
            "reminder_at": row[12],
            "outcome_id": row[13],
            "diagnosis": row[14],
            "metadata": meta or {},
        }

    async def list_seller_actions(
        self,
        seller_id: int,
        article: int,
        *,
        limit: int = 50,
    ) -> list[dict]:
        cur = await self.db.execute(
            "SELECT action_id FROM seller_actions "
            "WHERE seller_id=? AND article=? "
            "ORDER BY COALESCE(executed_at, accepted_at, 0) DESC LIMIT ?",
            (seller_id, article, limit),
        )
        ids = [r[0] for r in await cur.fetchall()]
        out = []
        for aid in ids:
            row = await self.get_seller_action(aid)
            if row:
                out.append(row)
        return out

    async def list_seller_actions_due(
        self,
        seller_id: int | None,
        now_ts: float,
    ) -> list[dict]:
        if seller_id is None:
            cur = await self.db.execute(
                "SELECT action_id FROM seller_actions "
                "WHERE status IN ('EXECUTED','CHECK_PENDING') "
                "AND check_after IS NOT NULL AND check_after<=?",
                (now_ts,),
            )
        else:
            cur = await self.db.execute(
                "SELECT action_id FROM seller_actions "
                "WHERE seller_id=? AND status IN ('EXECUTED','CHECK_PENDING') "
                "AND check_after IS NOT NULL AND check_after<=?",
                (seller_id, now_ts),
            )
        ids = [r[0] for r in await cur.fetchall()]
        out = []
        for aid in ids:
            row = await self.get_seller_action(aid)
            if row:
                out.append(row)
        return out


def _to_text(value) -> str | None:
    return None if value is None else str(value)
