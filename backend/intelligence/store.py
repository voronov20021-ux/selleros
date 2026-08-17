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
import time
from pathlib import Path

import aiosqlite

from backend.intelligence.interfaces import IIntelligenceStore
from backend.intelligence.learning import (
    ActionOutcome,
    LearningSignal,
    LearningSignalType,
    OutcomeDirection,
)
from backend.intelligence.outcomes import (
    RecommendationOutcome,
    OutcomeDirection as RecOutcomeDirection,
)
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
    ReviewAssessment,
    ReviewIssue,
    ReviewSentiment,
    ReviewSignal,
    ReviewSignalType,
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

    # ──────────────────────────── api call tracking ─────────────────────── #

    async def record_api_call(
        self,
        call_id: str,
        source_id: str,
        query: str | None,
        category: str | None,
        region: str | None,
        called_at: float,
    ) -> None:
        await self.db.execute(
            "INSERT OR IGNORE INTO api_calls (id, source_id, query, category, region, called_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (call_id, source_id, query, category, region, called_at),
        )
        await self.db.commit()

    async def count_api_calls(self, source_id: str, since_ts: float) -> int:
        cursor = await self.db.execute(
            "SELECT COUNT(*) FROM api_calls WHERE source_id = ? AND called_at >= ?",
            (source_id, since_ts),
        )
        row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def search_items_by_query(
        self,
        query: str,
        source_id: str | None = None,
        since_ts: float | None = None,
        limit: int = 50,
    ) -> list[KnowledgeItem]:
        conditions = ["json_extract(metadata, '$.query') = ?"]
        params: list = [query]
        if source_id is not None:
            conditions.append("source_id = ?")
            params.append(source_id)
        if since_ts is not None:
            conditions.append("collected_at >= ?")
            params.append(since_ts)
        params.append(limit)
        where = " AND ".join(conditions)
        cursor = await self.db.execute(
            f"SELECT id, source_id, source_url, collected_at, published_at, "
            f"item_type, category, region, period, confidence, content, metadata "
            f"FROM knowledge_items WHERE {where} "
            f"ORDER BY collected_at DESC LIMIT ?",
            tuple(params),
        )
        rows = await cursor.fetchall()
        return [self._row_to_item(r) for r in rows]

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

    # ──────────────────────────────── learning loop ─────────────────────── #

    async def save_action_outcome(self, outcome: ActionOutcome) -> None:
        await self.db.execute(
            """
            INSERT OR REPLACE INTO action_outcomes
                (id, user_hash, category, article, recommendation_type, action,
                 period_start, period_end, created_at,
                 metrics_before, metrics_after, outcome_direction, outcome_score,
                 confidence, evidence_ids, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                outcome.id,
                outcome.user_hash,
                outcome.category,
                outcome.article,
                outcome.recommendation_type,
                outcome.action,
                outcome.period_start,
                outcome.period_end,
                outcome.created_at,
                self._j(outcome.metrics_before),
                self._j(outcome.metrics_after),
                outcome.outcome_direction.value,
                outcome.outcome_score,
                outcome.confidence,
                self._j(outcome.evidence_ids),
                self._j(outcome.metadata),
            ),
        )
        await self.db.commit()

    async def get_action_outcome(self, outcome_id: str) -> ActionOutcome | None:
        cursor = await self.db.execute(
            "SELECT id, user_hash, category, article, recommendation_type, action, "
            "period_start, period_end, created_at, metrics_before, metrics_after, "
            "outcome_direction, outcome_score, confidence, evidence_ids, metadata "
            "FROM action_outcomes WHERE id = ?",
            (outcome_id,),
        )
        row = await cursor.fetchone()
        return self._row_to_outcome(row) if row else None

    async def search_action_outcomes(
        self,
        *,
        category: str | None = None,
        action: str | None = None,
        user_hash: str | None = None,
        since_ts: float | None = None,
        limit: int = 100,
    ) -> list[ActionOutcome]:
        conditions = ["1=1"]
        params: list = []
        if category is not None:
            conditions.append("category = ?")
            params.append(category)
        if action is not None:
            conditions.append("action = ?")
            params.append(action)
        if user_hash is not None:
            conditions.append("user_hash = ?")
            params.append(user_hash)
        if since_ts is not None:
            conditions.append("period_end >= ?")
            params.append(since_ts)
        params.append(limit)
        where = " AND ".join(conditions)
        cursor = await self.db.execute(
            f"SELECT id, user_hash, category, article, recommendation_type, action, "
            f"period_start, period_end, created_at, metrics_before, metrics_after, "
            f"outcome_direction, outcome_score, confidence, evidence_ids, metadata "
            f"FROM action_outcomes WHERE {where} "
            f"ORDER BY period_end DESC LIMIT ?",
            tuple(params),
        )
        rows = await cursor.fetchall()
        return [self._row_to_outcome(r) for r in rows]

    async def save_learning_signal(self, signal: LearningSignal) -> None:
        await self.db.execute(
            """
            INSERT OR REPLACE INTO learning_signals
                (id, outcome_id, signal_type, claim, confidence,
                 evidence_ids, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal.id,
                signal.outcome_id,
                signal.signal_type.value,
                signal.claim,
                signal.confidence,
                self._j(signal.evidence_ids),
                self._j(signal.metadata),
                signal.created_at,
            ),
        )
        await self.db.commit()

    async def search_learning_signals(
        self,
        *,
        outcome_id: str | None = None,
        signal_type: str | None = None,
        limit: int = 100,
    ) -> list[LearningSignal]:
        conditions = ["1=1"]
        params: list = []
        if outcome_id is not None:
            conditions.append("outcome_id = ?")
            params.append(outcome_id)
        if signal_type is not None:
            conditions.append("signal_type = ?")
            params.append(signal_type)
        params.append(limit)
        where = " AND ".join(conditions)
        cursor = await self.db.execute(
            f"SELECT id, outcome_id, signal_type, claim, confidence, "
            f"evidence_ids, metadata, created_at "
            f"FROM learning_signals WHERE {where} "
            f"ORDER BY created_at DESC LIMIT ?",
            tuple(params),
        )
        rows = await cursor.fetchall()
        return [self._row_to_learning_signal(r) for r in rows]

    async def find_learning_signal_by_source_outcome(
        self,
        source_outcome_id: str,
    ) -> LearningSignal | None:
        cursor = await self.db.execute(
            "SELECT id, outcome_id, signal_type, claim, confidence, "
            "evidence_ids, metadata, created_at "
            "FROM learning_signals "
            "WHERE json_extract(metadata, '$.source_outcome_id') = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (source_outcome_id,),
        )
        row = await cursor.fetchone()
        return self._row_to_learning_signal(row) if row else None

    def _row_to_outcome(self, row) -> ActionOutcome:
        return ActionOutcome(
            id=row[0],
            user_hash=row[1],
            category=row[2],
            article=row[3],
            recommendation_type=row[4],
            action=row[5],
            period_start=row[6],
            period_end=row[7],
            created_at=row[8],
            metrics_before=self._pj(row[9], {}),
            metrics_after=self._pj(row[10], {}),
            outcome_direction=OutcomeDirection(row[11]) if row[11] else OutcomeDirection.UNKNOWN,
            outcome_score=row[12],
            confidence=row[13],
            evidence_ids=self._pj(row[14], []),
            metadata=self._pj(row[15], {}),
        )

    def _row_to_learning_signal(self, row) -> LearningSignal:
        return LearningSignal(
            id=row[0],
            outcome_id=row[1],
            signal_type=LearningSignalType(row[2]),
            claim=row[3],
            confidence=row[4],
            evidence_ids=self._pj(row[5], []),
            metadata=self._pj(row[6], {}),
            created_at=row[7],
        )

    # ──────────────────────── recommendation outcomes ───────────────────── #

    async def save_recommendation_outcome(
        self, outcome: RecommendationOutcome,
    ) -> None:
        await self.db.execute(
            """
            INSERT OR REPLACE INTO recommendation_outcomes
                (id, user_hash, category, article, recommendation_type,
                 recommendation_action, recommendation_confidence, recommended_at,
                 action_taken, action_taken_at, period_start, period_end,
                 metrics_before, metrics_after, outcome_direction, outcome_score,
                 confidence, evidence_ids, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                outcome.id,
                outcome.user_hash,
                outcome.category,
                outcome.article,
                outcome.recommendation_type,
                outcome.recommendation_action,
                outcome.recommendation_confidence,
                outcome.recommended_at,
                outcome.action_taken,
                outcome.action_taken_at,
                outcome.period_start,
                outcome.period_end,
                self._j(outcome.metrics_before),
                self._j(outcome.metrics_after),
                outcome.outcome_direction.value,
                outcome.outcome_score,
                outcome.confidence,
                self._j(outcome.evidence_ids),
                self._j(outcome.metadata),
            ),
        )
        await self.db.commit()

    async def get_recommendation_outcome(
        self, outcome_id: str,
    ) -> RecommendationOutcome | None:
        cursor = await self.db.execute(
            "SELECT id, user_hash, category, article, recommendation_type, "
            "recommendation_action, recommendation_confidence, recommended_at, "
            "action_taken, action_taken_at, period_start, period_end, "
            "metrics_before, metrics_after, outcome_direction, outcome_score, "
            "confidence, evidence_ids, metadata "
            "FROM recommendation_outcomes WHERE id = ?",
            (outcome_id,),
        )
        row = await cursor.fetchone()
        return self._row_to_rec_outcome(row) if row else None

    async def search_recommendation_outcomes(
        self,
        *,
        category: str | None = None,
        article: str | None = None,
        recommendation_type: str | None = None,
        outcome_direction: str | None = None,
        days: int | None = 90,
        limit: int = 100,
    ) -> list[RecommendationOutcome]:
        import time as _time
        conditions = ["1=1"]
        params: list = []
        if category is not None:
            conditions.append("category = ?")
            params.append(category)
        if article is not None:
            conditions.append("article = ?")
            params.append(article)
        if recommendation_type is not None:
            conditions.append("recommendation_type = ?")
            params.append(recommendation_type)
        if outcome_direction is not None:
            conditions.append("outcome_direction = ?")
            params.append(outcome_direction)
        if days is not None and days > 0:
            conditions.append("recommended_at >= ?")
            params.append(_time.time() - days * 86400)
        params.append(limit)
        where = " AND ".join(conditions)
        cursor = await self.db.execute(
            f"SELECT id, user_hash, category, article, recommendation_type, "
            f"recommendation_action, recommendation_confidence, recommended_at, "
            f"action_taken, action_taken_at, period_start, period_end, "
            f"metrics_before, metrics_after, outcome_direction, outcome_score, "
            f"confidence, evidence_ids, metadata "
            f"FROM recommendation_outcomes WHERE {where} "
            f"ORDER BY recommended_at DESC LIMIT ?",
            tuple(params),
        )
        rows = await cursor.fetchall()
        return [self._row_to_rec_outcome(r) for r in rows]

    def _row_to_rec_outcome(self, row) -> RecommendationOutcome:
        return RecommendationOutcome(
            id=row[0],
            user_hash=row[1],
            category=row[2],
            article=row[3],
            recommendation_type=row[4],
            recommendation_action=row[5],
            recommendation_confidence=row[6],
            recommended_at=row[7],
            action_taken=row[8],
            action_taken_at=row[9],
            period_start=row[10],
            period_end=row[11],
            metrics_before=self._pj(row[12], {}),
            metrics_after=self._pj(row[13], {}),
            outcome_direction=(
                RecOutcomeDirection(row[14]) if row[14]
                else RecOutcomeDirection.UNKNOWN
            ),
            outcome_score=row[15],
            confidence=row[16],
            evidence_ids=self._pj(row[17], []),
            metadata=self._pj(row[18], {}),
        )

    # ──────────────────────────────── review intelligence ───────────────── #

    async def save_review_signal(self, signal: ReviewSignal) -> None:
        await self.db.execute(
            """
            INSERT OR REPLACE INTO review_signals
                (id, user_hash, article, category, signal_type, sentiment, claim,
                 confidence, source_ids, source_url, review_id, created_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal.id,
                signal.user_hash or "",
                signal.article,
                signal.category,
                signal.signal_type.value,
                signal.sentiment.value,
                signal.claim,
                signal.confidence,
                self._j(signal.source_ids),
                signal.source_url,
                signal.review_id,
                signal.created_at,
                self._j(signal.metadata),
            ),
        )
        await self.db.commit()

    async def save_review_issue(self, issue: ReviewIssue) -> None:
        await self.db.execute(
            """
            INSERT OR REPLACE INTO review_issues
                (id, user_hash, article, category, signal_type, sentiment, claim,
                 count, ratio, confidence, source_ids, created_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                issue.id,
                issue.user_hash or "",
                issue.article,
                issue.category,
                issue.signal_type.value,
                issue.sentiment.value,
                issue.claim,
                issue.count,
                issue.ratio,
                issue.confidence,
                self._j(issue.source_ids),
                issue.created_at,
                self._j(issue.metadata),
            ),
        )
        await self.db.commit()

    async def search_review_signals(
        self,
        *,
        user_hash: str | None = None,
        category: str | None = None,
        article: str | None = None,
        signal_type: str | None = None,
        since_ts: float | None = None,
        limit: int = 100,
    ) -> list[ReviewSignal]:
        conditions = ["1=1"]
        params: list = []
        if user_hash is not None:
            conditions.append("user_hash = ?")
            params.append(user_hash)
        if category is not None:
            conditions.append("category = ?")
            params.append(category)
        if article is not None:
            conditions.append("article = ?")
            params.append(article)
        if signal_type is not None:
            conditions.append("signal_type = ?")
            params.append(signal_type)
        if since_ts is not None:
            conditions.append("created_at >= ?")
            params.append(since_ts)
        params.append(limit)
        where = " AND ".join(conditions)
        cursor = await self.db.execute(
            f"SELECT id, user_hash, article, category, signal_type, sentiment, claim, "
            f"confidence, source_ids, source_url, review_id, created_at, metadata "
            f"FROM review_signals WHERE {where} "
            f"ORDER BY created_at DESC LIMIT ?",
            tuple(params),
        )
        rows = await cursor.fetchall()
        return [self._row_to_review_signal(r) for r in rows]

    async def search_review_issues(
        self,
        *,
        user_hash: str | None = None,
        category: str | None = None,
        article: str | None = None,
        signal_type: str | None = None,
        sentiment: str | None = None,
        min_count: int = 1,
        limit: int = 50,
    ) -> list[ReviewIssue]:
        conditions = ["count >= ?"]
        params: list = [min_count]
        if user_hash is not None:
            conditions.append("user_hash = ?")
            params.append(user_hash)
        if category is not None:
            conditions.append("category = ?")
            params.append(category)
        if article is not None:
            conditions.append("article = ?")
            params.append(article)
        if signal_type is not None:
            conditions.append("signal_type = ?")
            params.append(signal_type)
        if sentiment is not None:
            conditions.append("sentiment = ?")
            params.append(sentiment)
        params.append(limit)
        where = " AND ".join(conditions)
        cursor = await self.db.execute(
            f"SELECT id, user_hash, article, category, signal_type, sentiment, claim, "
            f"count, ratio, confidence, source_ids, created_at, metadata "
            f"FROM review_issues WHERE {where} "
            f"ORDER BY count DESC, confidence DESC LIMIT ?",
            tuple(params),
        )
        rows = await cursor.fetchall()
        return [self._row_to_review_issue(r) for r in rows]

    async def get_review_assessment(
        self,
        *,
        user_hash: str,
        category: str | None = None,
        article: str | None = None,
        days: int = 30,
    ) -> ReviewAssessment | None:
        since = time.time() - days * 86400 if days > 0 else None
        signals = await self.search_review_signals(
            user_hash=user_hash,
            category=category,
            article=article,
            since_ts=since,
            limit=200,
        )
        issues = await self.search_review_issues(
            user_hash=user_hash,
            category=category,
            article=article,
            min_count=1,
            limit=50,
        )
        if not signals and not issues:
            return None
        conf = 0.0
        if issues:
            conf = sum(i.confidence for i in issues) / len(issues)
        elif signals:
            conf = sum(s.confidence for s in signals) / len(signals)
        from backend.intelligence.reviews import (
            build_seller_actions,
            build_seller_problems,
        )
        problems = build_seller_problems(issues, signals)
        actions = build_seller_actions(problems)
        return ReviewAssessment(
            category=category,
            article=article,
            user_hash=user_hash,
            processed_count=len(signals),
            signals=signals,
            issues=issues,
            problems=problems,
            actions=actions,
            confidence=round(min(0.90, conf), 4),
            generated_at=time.time(),
        )

    def _row_to_review_signal(self, row) -> ReviewSignal:
        return ReviewSignal(
            id=row[0],
            user_hash=row[1] or None,
            article=row[2],
            category=row[3],
            signal_type=ReviewSignalType(row[4]),
            sentiment=ReviewSentiment(row[5]),
            claim=row[6],
            confidence=row[7],
            source_ids=self._pj(row[8], []),
            source_url=row[9],
            review_id=row[10],
            created_at=row[11],
            metadata=self._pj(row[12], {}),
        )

    def _row_to_review_issue(self, row) -> ReviewIssue:
        return ReviewIssue(
            id=row[0],
            user_hash=row[1] or None,
            article=row[2],
            category=row[3],
            signal_type=ReviewSignalType(row[4]),
            sentiment=ReviewSentiment(row[5]),
            claim=row[6],
            count=row[7],
            ratio=row[8],
            confidence=row[9],
            source_ids=self._pj(row[10], []),
            created_at=row[11],
            metadata=self._pj(row[12], {}),
        )
