"""
market_event_engine.py — сбор и хранение рыночных событий.

MarketEventEngine координирует:
    1. EventSourceAdapter.fetch()     — получить и классифицировать события
    2. Дедупликацию через EvidenceEngine.fingerprint / is_duplicate
    3. IntelligenceStore.save_market_event() — сохранить уникальные события
    4. EvidenceEngine.ingest()        — создать Evidence для каждого события

Cooldown (TTL_HOURS):
    Один и тот же query + category + region не запрашивается чаще одного
    раза в TTL_HOURS часов. Если в store уже есть свежие KnowledgeItem от
    того же источника — возвращаем кешированные MarketEvent без HTTP-запроса.

Строгость:
    MarketEvent означает «существует подтверждённое внешнее событие»,
    НЕ «это событие влияет на продажи». impact_direction указывает
    на вероятное направление влияния, не на измеренный результат.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid

from backend.intelligence.event_sources.base import EventSourceAdapter
from backend.intelligence.evidence.engine import EvidenceEngine
from backend.intelligence.interfaces import IIntelligenceStore
from backend.intelligence.models import (
    DataSource,
    Evidence,
    EvidenceType,
    ImpactDirection,
    ItemType,
    KnowledgeItem,
    MarketEvent,
    SourceType,
)

log = logging.getLogger("selleros.intelligence.market_event_engine")

#: Период cooldown — повторный запрос того же query+category+region
#: не раньше, чем через TTL_HOURS часов.
TTL_HOURS: int = 24
_TTL_SECONDS: float = TTL_HOURS * 3600


class MarketEventEngine:
    """
    Движок сбора, дедупликации и хранения рыночных событий.

    Использование:

        engine = MarketEventEngine(store=store, ev_engine=ev_engine)
        events = await engine.collect_and_ingest(
            query="распродажа WB осень 2026",
            category="Одежда",
        )
    """

    def __init__(
        self,
        store: IIntelligenceStore,
        ev_engine: EvidenceEngine,
        adapter: EventSourceAdapter | None = None,
    ) -> None:
        self._store    = store
        self._ev       = ev_engine
        self._adapter  = adapter

    # ─────────────────────────────── публичное API ──────────────────────── #

    async def collect(
        self,
        query: str,
        category: str | None = None,
        region: str = "RU",
        limit: int = 10,
    ) -> list[MarketEvent]:
        """
        Получить MarketEvent через адаптер с учётом cooldown.

        Если для данного query+category+region уже есть свежие события
        в store (возраст < TTL_HOURS) — возвращает кешированные данные
        без HTTP-запроса.

        Если адаптер не задан — возвращает события только из store.
        """
        cache_key = self._cache_key(query, category, region)
        cutoff_ts = time.time() - _TTL_SECONDS

        cached = await self._store.list_market_events(
            category=category,
            after_ts=cutoff_ts,
            limit=limit,
        )
        # Фильтруем по query (store не фильтрует по query)
        cached = [
            e for e in cached
            if (e.metadata or {}).get("query") == query
            and (not region or e.region == region)
        ]

        if cached:
            log.info(
                "MarketEventEngine: cooldown hit — %d кешированных событий для %r",
                len(cached), query,
            )
            return cached

        if self._adapter is None:
            log.debug("MarketEventEngine: адаптер не задан, возвращаем []")
            return []

        if not await self._adapter.is_available():
            log.warning("MarketEventEngine: адаптер %r недоступен", self._adapter.source_id)
            return []

        events = await self._adapter.fetch(
            query=query,
            category=category,
            region=region,
            limit=limit,
        )
        return events

    async def ingest(self, events: list[MarketEvent]) -> int:
        """
        Дедуплицировать и сохранить список MarketEvent.

        Для каждого события:
            1. Создать «зеркальный» KnowledgeItem для fingerprint-проверки.
            2. Если дубликат — пропустить.
            3. Сохранить KnowledgeItem + MarketEvent в store.
            4. Создать Evidence через EvidenceEngine.

        Возвращает количество сохранённых (новых) событий.
        """
        # Гарантируем регистрацию всех source_id в data_sources (FK constraint)
        seen_sources: set[str] = set()
        for event in events:
            if event.source_id not in seen_sources:
                await self._ensure_source(event.source_id)
                seen_sources.add(event.source_id)

        saved = 0
        for event in events:
            ki = self._event_to_knowledge_item(event)

            if await self._ev.is_duplicate(ki):
                log.debug(
                    "MarketEventEngine: дубликат пропущен — %r",
                    event.title[:60],
                )
                continue

            try:
                await self._store.save_item(ki)
                await self._store.save_market_event(event)
                saved += 1
            except Exception as exc:
                log.warning(
                    "MarketEventEngine: ошибка сохранения события %r: %s",
                    event.title[:60], exc,
                )
                continue

            try:
                evidence = self._event_to_evidence(event, ki.id)
                await self._store.save_evidence(evidence)
                log.debug(
                    "MarketEventEngine: Evidence создан для %r", event.title[:60]
                )
            except Exception as exc:
                log.warning(
                    "MarketEventEngine: не удалось создать Evidence для %r: %s",
                    event.title[:60], exc,
                )

        log.info(
            "MarketEventEngine.ingest: %d/%d событий сохранено",
            saved, len(events),
        )
        return saved

    async def collect_and_ingest(
        self,
        query: str,
        category: str | None = None,
        region: str = "RU",
        limit: int = 10,
    ) -> list[MarketEvent]:
        """
        Получить события через адаптер и сразу сохранить новые.

        Возвращает все события (включая уже кешированные).
        """
        events = await self.collect(
            query=query, category=category, region=region, limit=limit,
        )
        if events:
            await self.ingest(events)
        return events

    # ─────────────────────────────── helpers ────────────────────────────── #

    async def _ensure_source(self, source_id: str) -> None:
        """INSERT OR IGNORE DataSource для source_id, если не зарегистрирован."""
        existing = await self._store.get_source(source_id)
        if existing is not None:
            return
        ds = DataSource(
            id=source_id,
            name=source_id.replace("_", " ").title(),
            source_type=SourceType.PUBLIC_API,
            authority=0.55,
            freshness_hours=6,
            capabilities=["market_news"],
        )
        await self._store.save_source(ds)
        log.debug("MarketEventEngine: авто-зарегистрирован источник %r", source_id)

    @staticmethod
    def _cache_key(query: str, category: str | None, region: str) -> str:
        raw = f"{query}|{category or ''}|{region}"
        return hashlib.sha256(raw.encode()).hexdigest()

    @staticmethod
    def _event_to_knowledge_item(event: MarketEvent) -> KnowledgeItem:
        """
        Создать KnowledgeItem из MarketEvent для fingerprint-дедупликации.

        source_url берётся из event.metadata["source_url"].
        content = title + description (для fingerprint).
        """
        source_url = (event.metadata or {}).get("source_url")
        content_parts = [event.title]
        if event.description:
            content_parts.append(event.description)
        content = "\n".join(content_parts)

        return KnowledgeItem(
            id=str(uuid.uuid4()),
            source_id=event.source_id,
            source_url=source_url,
            collected_at=event.created_at,
            published_at=event.event_date,
            item_type=ItemType.FACT,
            category=event.category,
            region=event.region,
            period=None,
            confidence=event.confidence,
            content=content,
            metadata={
                "market_event_id":   event.id,
                "event_type":        event.event_type.value,
                "impact_direction":  event.impact_direction.value
                                     if event.impact_direction else None,
                **(event.metadata or {}),
            },
        )

    @staticmethod
    def _event_to_evidence(event: MarketEvent, knowledge_item_id: str) -> Evidence:
        """
        Создать Evidence типа FACT из MarketEvent.

        Claim формируется из title и event_type — коротко и читаемо.
        """
        impact_str = ""
        if event.impact_direction and event.impact_direction != ImpactDirection.NEUTRAL:
            impact_str = f" (влияние: {event.impact_direction.value})"

        claim = (
            f"[{event.event_type.value.upper()}]{impact_str} "
            f"{event.title[:150]}"
        )

        return Evidence(
            id=str(uuid.uuid4()),
            knowledge_item_id=knowledge_item_id,
            evidence_type=EvidenceType.FACT,
            claim=claim,
            created_at=time.time(),
            confidence=event.confidence,
            supporting_data={
                "signal_type":       "market_event",
                "event_type":        event.event_type.value,
                "impact_direction":  event.impact_direction.value
                                     if event.impact_direction else "neutral",
                "category":          event.category,
                "region":            event.region,
                "source_url":        (event.metadata or {}).get("source_url"),
                "query":             (event.metadata or {}).get("query"),
                "event_date":        event.event_date,
            },
        )
