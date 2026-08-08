"""
store.py — SQLite-реализация IIntelligenceStore.

Паттерн идентичен backend/memory/store.py:
    - raw SQL, никакого ORM;
    - connect() / close() управляют соединением;
    - WAL-режим для конкурентного доступа;
    - schema.sql загружается один раз при первом старте.

Замена на PostgreSQL = создать PgIntelligenceStore, реализующий
тот же IIntelligenceStore, и передать его в конструкторы
EvidenceEngine и SourceRegistry. Ни один вызывающий файл не меняется.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import aiosqlite

from backend.intelligence.interfaces import IIntelligenceStore
from backend.intelligence.models import (
    ChangeType,
    DataSource,
    Evidence,
    EvidenceType,
    EventType,
    ImpactDirection,
    ItemType,
    KnowledgeItem,
    MarketEvent,
    SeasonalityRecord,
    SellerObservation,
    SourceType,
    TrendDirection,
    TrendRecord,
)

log = logging.getLogger("selleros.intelligence.store")

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class IntelligenceStore(IIntelligenceStore):
    """
    SQLite-реализация репозитория Intelligence Layer.

    db_path — путь к файлу БД, создаётся автоматически.
    Рекомендуемое имя: data/intelligence.db (отдельный файл от argus.db,
    чтобы user-данные и market-intelligence не смешивались).
    """

    def __init__(self, db_path: str = "data/intelligence.db") -> None:
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError(
                "IntelligenceStore не подключён. Вызовите await store.connect() перед использованием."
            )
        return self._db

    # ──────────────────────────────── lifecycle ─────────────────────────── #

    async def connect(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")

        schema = _SCHEMA_PATH.read_text(encoding="utf-8")
        await self._db.executescript(schema)
        await self._db.commit()

        await self._seed_builtin_sources()

        log.info("Intelligence Store подключён: %s", self.db_path)

    async def _seed_builtin_sources(self) -> None:
        """
        Предзаполнить встроенные системные источники.

        "user_generated" необходим для KnowledgeItem, создаваемых из
        SellerObservation (обезличенные наблюдения продавцов). Без него
        FK knowledge_items.source_id → data_sources.id нарушается.

        Все builtin-источники добавляются с INSERT OR IGNORE — повторный
        вызов (каждый старт бота) безопасен.
        """
        builtins = [
            (
                "user_generated",
                "User-generated observations",
                SourceType.USER_GENERATED.value,
                0.60,   # authority: наблюдения продавцов надёжны, но не верифицированы
                0,      # freshness_hours: 0 = данные не устаревают (они исторические)
                "[]",   # capabilities: не подключается через SourceRegistry
                None,   # base_url
            ),
            (
                "manual",
                "Manual Argus data",
                SourceType.MANUAL.value,
                0.95,   # authority: внесено командой вручную
                0,
                "[]",
                None,
            ),
        ]

        for row in builtins:
            await self._db.execute(
                """
                INSERT OR IGNORE INTO data_sources
                    (id, name, source_type, authority, freshness_hours,
                     capabilities, base_url, is_active, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, '{}')
                """,
                row,
            )

        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None
            log.info("Intelligence Store закрыт.")

    # ──────────────────────────────── helpers ───────────────────────────── #

    @staticmethod
    def _j(obj) -> str:
        """Сериализовать dict/list в JSON для хранения в TEXT-колонке."""
        return json.dumps(obj, ensure_ascii=False)

    @staticmethod
    def _pj(text: str | None, default):
        """Десериализовать JSON-колонку; вернуть default при пустом значении."""
        if not text:
            return default
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return default

    # ──────────────────────────────── sources ───────────────────────────── #

    async def save_source(self, source: DataSource) -> None:
        await self.db.execute(
            """
            INSERT INTO data_sources
                (id, name, source_type, authority, freshness_hours,
                 capabilities, base_url, is_active, last_fetched_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name            = excluded.name,
                source_type     = excluded.source_type,
                authority       = excluded.authority,
                freshness_hours = excluded.freshness_hours,
                capabilities    = excluded.capabilities,
                base_url        = excluded.base_url,
                is_active       = excluded.is_active,
                last_fetched_at = excluded.last_fetched_at,
                metadata        = excluded.metadata
            """,
            (
                source.id,
                source.name,
                source.source_type.value,
                source.authority,
                source.freshness_hours,
                self._j(source.capabilities),
                source.base_url,
                int(source.is_active),
                source.last_fetched_at,
                self._j(source.metadata),
            ),
        )
        await self.db.commit()

    async def get_source(self, source_id: str) -> DataSource | None:
        cursor = await self.db.execute(
            "SELECT id, name, source_type, authority, freshness_hours, "
            "capabilities, base_url, is_active, last_fetched_at, metadata "
            "FROM data_sources WHERE id = ?",
            (source_id,),
        )
        row = await cursor.fetchone()
        return self._row_to_source(row) if row else None

    async def list_sources(self, *, active_only: bool = True) -> list[DataSource]:
        query = (
            "SELECT id, name, source_type, authority, freshness_hours, "
            "capabilities, base_url, is_active, last_fetched_at, metadata "
            "FROM data_sources"
        )
        params: tuple = ()
        if active_only:
            query += " WHERE is_active = 1"
        cursor = await self.db.execute(query, params)
        rows = await cursor.fetchall()
        return [self._row_to_source(r) for r in rows]

    def _row_to_source(self, row) -> DataSource:
        return DataSource(
            id=row[0],
            name=row[1],
            source_type=SourceType(row[2]),
            authority=row[3],
            freshness_hours=row[4],
            capabilities=self._pj(row[5], []),
            base_url=row[6],
            is_active=bool(row[7]),
            last_fetched_at=row[8],
            metadata=self._pj(row[9], {}),
        )

    # ──────────────────────────────── knowledge items ───────────────────── #

    async def save_item(self, item: KnowledgeItem) -> None:
        await self.db.execute(
            """
            INSERT OR IGNORE INTO knowledge_items
                (id, source_id, source_url, collected_at, published_at,
                 item_type, category, region, period, confidence, content, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.id,
                item.source_id,
                item.source_url,
                item.collected_at,
                item.published_at,
                item.item_type.value,
                item.category,
                item.region,
                item.period,
                item.confidence,
                item.content,
                self._j(item.metadata),
            ),
        )
        await self.db.commit()

    async def get_item(self, item_id: str) -> KnowledgeItem | None:
        cursor = await self.db.execute(
            "SELECT id, source_id, source_url, collected_at, published_at, "
            "item_type, category, region, period, confidence, content, metadata "
            "FROM knowledge_items WHERE id = ?",
            (item_id,),
        )
        row = await cursor.fetchone()
        return self._row_to_item(row) if row else None

    async def search_items(
        self,
        *,
        source_id: str | None = None,
        category: str | None = None,
        region: str | None = None,
        min_confidence: float = 0.0,
        limit: int = 50,
    ) -> list[KnowledgeItem]:
        conditions = ["confidence >= ?"]
        params: list = [min_confidence]

        if source_id is not None:
            conditions.append("source_id = ?")
            params.append(source_id)
        if category is not None:
            conditions.append("category = ?")
            params.append(category)
        if region is not None:
            conditions.append("region = ?")
            params.append(region)

        where = " AND ".join(conditions)
        params.append(limit)

        cursor = await self.db.execute(
            f"SELECT id, source_id, source_url, collected_at, published_at, "
            f"item_type, category, region, period, confidence, content, metadata "
            f"FROM knowledge_items WHERE {where} "
            f"ORDER BY collected_at DESC LIMIT ?",
            tuple(params),
        )
        rows = await cursor.fetchall()
        return [self._row_to_item(r) for r in rows]

    def _row_to_item(self, row) -> KnowledgeItem:
        return KnowledgeItem(
            id=row[0],
            source_id=row[1],
            source_url=row[2],
            collected_at=row[3],
            published_at=row[4],
            item_type=ItemType(row[5]),
            category=row[6],
            region=row[7],
            period=row[8],
            confidence=row[9],
            content=row[10],
            metadata=self._pj(row[11], {}),
        )

    # ──────────────────────────────── evidence ──────────────────────────── #

    async def save_evidence(self, evidence: Evidence) -> None:
        await self.db.execute(
            """
            INSERT OR IGNORE INTO evidence
                (id, knowledge_item_id, evidence_type, claim,
                 supporting_data, confidence, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence.id,
                evidence.knowledge_item_id,
                evidence.evidence_type.value,
                evidence.claim,
                self._j(evidence.supporting_data),
                evidence.confidence,
                evidence.created_at,
            ),
        )
        await self.db.commit()

    async def get_evidence(self, evidence_id: str) -> Evidence | None:
        cursor = await self.db.execute(
            "SELECT id, knowledge_item_id, evidence_type, claim, "
            "supporting_data, confidence, created_at "
            "FROM evidence WHERE id = ?",
            (evidence_id,),
        )
        row = await cursor.fetchone()
        return self._row_to_evidence(row) if row else None

    async def retrieve_evidence(
        self,
        *,
        evidence_type: EvidenceType | None = None,
        category: str | None = None,
        min_confidence: float = 0.3,
        limit: int = 20,
    ) -> list[Evidence]:
        """
        Выборка evidence для Argus reasoning.

        category-фильтр применяется через JOIN с knowledge_items,
        где category хранится на уровне исходной записи.
        """
        if category is not None:
            base_query = (
                "SELECT e.id, e.knowledge_item_id, e.evidence_type, e.claim, "
                "e.supporting_data, e.confidence, e.created_at "
                "FROM evidence e "
                "JOIN knowledge_items ki ON ki.id = e.knowledge_item_id "
                "WHERE e.confidence >= ?"
            )
            params: list = [min_confidence]
            if evidence_type is not None:
                base_query += " AND e.evidence_type = ?"
                params.append(evidence_type.value)
            base_query += " AND ki.category = ?"
            params.append(category)
            base_query += " ORDER BY e.confidence DESC LIMIT ?"
            params.append(limit)
        else:
            base_query = (
                "SELECT id, knowledge_item_id, evidence_type, claim, "
                "supporting_data, confidence, created_at "
                "FROM evidence WHERE confidence >= ?"
            )
            params = [min_confidence]
            if evidence_type is not None:
                base_query += " AND evidence_type = ?"
                params.append(evidence_type.value)
            base_query += " ORDER BY confidence DESC LIMIT ?"
            params.append(limit)

        cursor = await self.db.execute(base_query, tuple(params))
        rows = await cursor.fetchall()
        return [self._row_to_evidence(r) for r in rows]

    def _row_to_evidence(self, row) -> Evidence:
        return Evidence(
            id=row[0],
            knowledge_item_id=row[1],
            evidence_type=EvidenceType(row[2]),
            claim=row[3],
            supporting_data=self._pj(row[4], {}),
            confidence=row[5],
            created_at=row[6],
        )

    # ──────────────────────────────── observations ──────────────────────── #

    async def save_observation(self, obs: SellerObservation) -> None:
        await self.db.execute(
            """
            INSERT OR IGNORE INTO seller_observations
                (id, user_hash, article, created_at, change_type, category,
                 before_value, after_value, period_start, period_end,
                 outcome_sales_delta, outcome_orders_delta,
                 outcome_rating_delta, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                obs.id,
                obs.user_hash,
                obs.article,
                obs.created_at,
                obs.change_type.value,
                obs.category,
                obs.before_value,
                obs.after_value,
                obs.period_start,
                obs.period_end,
                obs.outcome_sales_delta,
                obs.outcome_orders_delta,
                obs.outcome_rating_delta,
                obs.notes,
            ),
        )
        await self.db.commit()

    async def list_observations(
        self,
        *,
        category: str | None = None,
        change_type: str | None = None,
        limit: int = 100,
    ) -> list[SellerObservation]:
        conditions = ["1=1"]
        params: list = []

        if category is not None:
            conditions.append("category = ?")
            params.append(category)
        if change_type is not None:
            conditions.append("change_type = ?")
            params.append(change_type)

        where = " AND ".join(conditions)
        params.append(limit)

        cursor = await self.db.execute(
            f"SELECT id, user_hash, article, created_at, change_type, category, "
            f"before_value, after_value, period_start, period_end, "
            f"outcome_sales_delta, outcome_orders_delta, outcome_rating_delta, notes "
            f"FROM seller_observations WHERE {where} "
            f"ORDER BY created_at DESC LIMIT ?",
            tuple(params),
        )
        rows = await cursor.fetchall()
        return [self._row_to_obs(r) for r in rows]

    def _row_to_obs(self, row) -> SellerObservation:
        return SellerObservation(
            id=row[0],
            user_hash=row[1],
            article=row[2],
            created_at=row[3],
            change_type=ChangeType(row[4]),
            category=row[5],
            before_value=row[6],
            after_value=row[7],
            period_start=row[8],
            period_end=row[9],
            outcome_sales_delta=row[10],
            outcome_orders_delta=row[11],
            outcome_rating_delta=row[12],
            notes=row[13],
        )

    # ──────────────────────────────── seasonality ───────────────────────── #

    async def save_seasonality(self, record: SeasonalityRecord) -> None:
        await self.db.execute(
            """
            INSERT OR IGNORE INTO seasonality_records
                (id, category, region, month, week, demand_index,
                 source_id, period_year, confidence, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.category,
                record.region,
                record.month,
                record.week,
                record.demand_index,
                record.source_id,
                record.period_year,
                record.confidence,
                record.created_at,
            ),
        )
        await self.db.commit()

    async def get_seasonality(
        self,
        category: str,
        region: str,
        month: int,
    ) -> list[SeasonalityRecord]:
        cursor = await self.db.execute(
            "SELECT id, category, region, month, week, demand_index, "
            "source_id, period_year, confidence, created_at "
            "FROM seasonality_records "
            "WHERE category = ? AND region = ? AND month = ? "
            "ORDER BY period_year DESC",
            (category, region, month),
        )
        rows = await cursor.fetchall()
        return [
            SeasonalityRecord(
                id=r[0],
                category=r[1],
                region=r[2],
                month=r[3],
                week=r[4],
                demand_index=r[5],
                source_id=r[6],
                period_year=r[7],
                confidence=r[8],
                created_at=r[9],
            )
            for r in rows
        ]

    # ──────────────────────────────── trends ────────────────────────────── #

    async def save_trend(self, record: TrendRecord) -> None:
        await self.db.execute(
            """
            INSERT OR IGNORE INTO trend_records
                (id, source_id, period_start, period_end, direction,
                 created_at, category, query, region, change_pct,
                 confidence, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.source_id,
                record.period_start,
                record.period_end,
                record.direction.value,
                record.created_at,
                record.category,
                record.query,
                record.region,
                record.change_pct,
                record.confidence,
                self._j(record.metadata),
            ),
        )
        await self.db.commit()

    async def list_trends(
        self,
        *,
        category: str | None = None,
        query: str | None = None,
        region: str | None = None,
        limit: int = 50,
    ) -> list[TrendRecord]:
        conditions = ["1=1"]
        params: list = []

        if category is not None:
            conditions.append("category = ?")
            params.append(category)
        if query is not None:
            conditions.append("query = ?")
            params.append(query)
        if region is not None:
            conditions.append("region = ?")
            params.append(region)

        where = " AND ".join(conditions)
        params.append(limit)

        cursor = await self.db.execute(
            f"SELECT id, source_id, period_start, period_end, direction, "
            f"created_at, category, query, region, change_pct, confidence, metadata "
            f"FROM trend_records WHERE {where} "
            f"ORDER BY period_start DESC LIMIT ?",
            tuple(params),
        )
        rows = await cursor.fetchall()
        return [
            TrendRecord(
                id=r[0],
                source_id=r[1],
                period_start=r[2],
                period_end=r[3],
                direction=TrendDirection(r[4]),
                created_at=r[5],
                category=r[6],
                query=r[7],
                region=r[8],
                change_pct=r[9],
                confidence=r[10],
                metadata=self._pj(r[11], {}),
            )
            for r in rows
        ]

    # ──────────────────────────────── market events ─────────────────────── #

    async def save_market_event(self, event: MarketEvent) -> None:
        await self.db.execute(
            """
            INSERT OR IGNORE INTO market_events
                (id, event_type, title, source_id, event_date, created_at,
                 description, category, region, impact_direction,
                 confidence, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.event_type.value,
                event.title,
                event.source_id,
                event.event_date,
                event.created_at,
                event.description,
                event.category,
                event.region,
                event.impact_direction.value if event.impact_direction else None,
                event.confidence,
                self._j(event.metadata),
            ),
        )
        await self.db.commit()

    async def list_market_events(
        self,
        *,
        category: str | None = None,
        event_type: str | None = None,
        after_ts: float | None = None,
        limit: int = 50,
    ) -> list[MarketEvent]:
        conditions = ["1=1"]
        params: list = []

        if category is not None:
            conditions.append("category = ?")
            params.append(category)
        if event_type is not None:
            conditions.append("event_type = ?")
            params.append(event_type)
        if after_ts is not None:
            conditions.append("event_date >= ?")
            params.append(after_ts)

        where = " AND ".join(conditions)
        params.append(limit)

        cursor = await self.db.execute(
            f"SELECT id, event_type, title, source_id, event_date, created_at, "
            f"description, category, region, impact_direction, confidence, metadata "
            f"FROM market_events WHERE {where} "
            f"ORDER BY event_date DESC LIMIT ?",
            tuple(params),
        )
        rows = await cursor.fetchall()
        return [
            MarketEvent(
                id=r[0],
                event_type=EventType(r[1]),
                title=r[2],
                source_id=r[3],
                event_date=r[4],
                created_at=r[5],
                description=r[6],
                category=r[7],
                region=r[8],
                impact_direction=ImpactDirection(r[9]) if r[9] else None,
                confidence=r[10],
                metadata=self._pj(r[11], {}),
            )
            for r in rows
        ]
