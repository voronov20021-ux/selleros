"""
category_intelligence.py — агрегатор аналитики по категории WB.

CategoryIntelligence.analyze(category, region, limit) собирает все
имеющиеся сигналы о категории из Intelligence Layer и возвращает
CategoryContext — готовый срез знаний без рекомендаций и решений.

Что делает:
    1. Проверяет TTL — если в store уже есть свежие данные для
       category+region (возраст < TTL_HOURS), полностью пропускает
       сетевые запросы и возвращает агрегат из существующих записей.
    2. Собирает demand-сигналы через SearchService (детерминированные
       запросы из _build_queries).
    3. Строит TrendRecord через TrendEngine.
    4. Строит SeasonalityRecord через SeasonalityEngine.
    5. Собирает MarketEvent через MarketEventEngine.
    6. Читает накопленные Evidence из IntelligenceStore.
    7. Разделяет MarketEvent по типам (regulation / platform / competitor).
    8. Вычисляет сводный confidence.

Что НЕ делает:
    - НЕ генерирует рекомендации.
    - НЕ вызывает Telegram, WB, Ozon.
    - НЕ создаёт synthetic/fake signals.
    - НЕ дублирует existing механизмы дедупликации.
"""

from __future__ import annotations

import logging
import time
import statistics
from dataclasses import dataclass, field

from backend.intelligence.cost_guard import CACHE_TTL_DAYS, YandexCostGuard
from backend.intelligence.evidence.engine import EvidenceEngine
from backend.intelligence.interfaces import IIntelligenceStore
from backend.intelligence.market_event_engine import MarketEventEngine
from backend.intelligence.models import (
    Evidence,
    EventType,
    KnowledgeItem,
    MarketEvent,
    SeasonalityRecord,
    TrendRecord,
)
from backend.intelligence.search_service import SearchService
from backend.intelligence.seasonality_engine import SeasonalityEngine
from backend.intelligence.trend_engine import TrendEngine

log = logging.getLogger("selleros.intelligence.category_intelligence")

#: TTL совпадает с CostGuard CACHE_TTL_DAYS (7 дней).
#: Повторный Yandex HTTP для той же category+region не раньше TTL.
TTL_DAYS: int = CACHE_TTL_DAYS
TTL_HOURS: int = TTL_DAYS * 24  # backwards-compat для тестов
_TTL_SECONDS: float = TTL_DAYS * 86400.0

#: MVP: один initial market probe на cache miss.
_MAX_SEARCH_QUERIES: int = 1

#: Минимальный confidence для включения Evidence в CategoryContext
_MIN_EVIDENCE_CONFIDENCE: float = 0.30


@dataclass
class CategoryContext:
    """
    Срез знаний Intelligence Layer об одной категории WB.

    Поля — только агрегированные факты и сигналы.
    Рекомендации и решения — не здесь.

    demand_signals     — KnowledgeItem с данными о спросе (found_phrase, домены)
    trend_signals      — TrendRecord для категории
    seasonal_signals   — профиль сезонности {month: demand_index}
    market_events      — все MarketEvent (все типы)
    regulation_events  — MarketEvent типа REGULATION
    platform_events    — MarketEvent типа PLATFORM
    competitor_events  — MarketEvent типа COMPETITOR
    evidence           — Evidence из EvidenceEngine (после дедупликации)
    confidence         — сводный confidence контекста (0..1)
    generated_at       — unix timestamp создания
    from_cache         — True если данные из TTL-кеша, False если свежий сбор
    """

    category: str
    region: str
    demand_signals: list[KnowledgeItem] = field(default_factory=list)
    trend_signals: list[TrendRecord] = field(default_factory=list)
    seasonal_signals: dict[int, float] = field(default_factory=dict)
    market_events: list[MarketEvent] = field(default_factory=list)
    regulation_events: list[MarketEvent] = field(default_factory=list)
    platform_events: list[MarketEvent] = field(default_factory=list)
    competitor_events: list[MarketEvent] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    confidence: float = 0.0
    generated_at: float = field(default_factory=time.time)
    from_cache: bool = False


def _probe_query(category: str) -> str:
    """Единый query для initial market probe (один HTTP на cache miss)."""
    return category.lower().strip()


def _build_queries(category: str) -> list[str]:
    """
    Детерминированный список поисковых запросов для категории.

    MVP: ровно один query (= category subject). Сохранено для совместимости.
    """
    return [_probe_query(category)][:_MAX_SEARCH_QUERIES]


def _split_events(
    events: list[MarketEvent],
) -> tuple[list[MarketEvent], list[MarketEvent], list[MarketEvent]]:
    """
    Разбить список MarketEvent на (regulation, platform, competitor).
    Возвращает три списка. Элемент может попасть только в один.
    """
    regulation: list[MarketEvent] = []
    platform:   list[MarketEvent] = []
    competitor: list[MarketEvent] = []

    for ev in events:
        if ev.event_type == EventType.REGULATION:
            regulation.append(ev)
        elif ev.event_type == EventType.PLATFORM:
            platform.append(ev)
        elif ev.event_type == EventType.COMPETITOR:
            competitor.append(ev)

    return regulation, platform, competitor


def _compute_confidence(
    demand_signals: list[KnowledgeItem],
    trend_signals: list[TrendRecord],
    seasonal_signals: dict[int, float],
    evidence: list[Evidence],
    from_cache: bool,
) -> float:
    """
    Консервативный сводный confidence CategoryContext.

    Базовый: 0.0.
    +0.10 за каждый непустой тип данных (demand / trend / seasonality / evidence).
    Среднее confidence по evidence (если есть).
    Penalty -0.05 если данные из кеша (могут быть чуть устаревшие).
    Итог: min(0.80, результат).
    """
    score = 0.0
    if demand_signals:
        score += 0.10
    if trend_signals:
        score += 0.10
    if seasonal_signals:
        score += 0.10
    if evidence:
        avg_ev = statistics.mean(e.confidence for e in evidence)
        score += avg_ev * 0.60   # вклад качества evidence

    if from_cache:
        score -= 0.05

    return round(max(0.0, min(0.80, score)), 4)


class CategoryIntelligence:
    """
    Агрегатор аналитики по категории WB.

    analyze(category, region, limit) → CategoryContext
    """

    def __init__(
        self,
        store: IIntelligenceStore,
        ev_engine: EvidenceEngine,
        search_svc: SearchService,
        trend_engine: TrendEngine,
        seasonality_engine: SeasonalityEngine,
        market_event_engine: MarketEventEngine,
        cost_guard: YandexCostGuard | None = None,
    ) -> None:
        self._store       = store
        self._ev          = ev_engine
        self._search      = search_svc
        self._trend       = trend_engine
        self._seasonality = seasonality_engine
        self._market      = market_event_engine
        # cost_guard оставлен для DI/backwards-compat; HTTP gate — в SearchService.
        self._guard       = cost_guard

    async def analyze(
        self,
        category: str,
        region: str = "RU",
        limit: int = 20,
    ) -> CategoryContext:
        """
        Собрать и вернуть срез знаний о категории.

        При наличии свежих данных в store (возраст < TTL_DAYS) —
        пропускает HTTP-запросы и возвращает агрегат из кеша.
        """
        from_cache = await self._is_fresh(category, region)

        if not from_cache:
            await self._collect_fresh(category, region, limit)
        else:
            log.info(
                "CategoryIntelligence: TTL hit для %r/%s, используем кеш",
                category, region,
            )

        ctx = await self._build_context(category, region, limit, from_cache)
        log.info(
            "CategoryIntelligence: %r/%s — demand=%d trend=%d seas=%d "
            "events=%d evidence=%d conf=%.2f cache=%s",
            category, region,
            len(ctx.demand_signals), len(ctx.trend_signals),
            len(ctx.seasonal_signals), len(ctx.market_events),
            len(ctx.evidence), ctx.confidence, from_cache,
        )
        return ctx

    # ──────────────────────────── private ─────────────────────────────────── #

    async def _is_fresh(self, category: str, region: str) -> bool:
        """
        Проверить TTL: есть ли в store свежие KnowledgeItem для category+region?
        """
        cutoff = time.time() - _TTL_SECONDS
        items = await self._store.search_items(
            category=category,
            region=region,
            limit=1,
        )
        return bool(items and items[0].collected_at >= cutoff)

    async def initial_market_probe(
        self,
        category: str,
        region: str = "RU",
    ) -> list[KnowledgeItem]:
        """
        Один initial Yandex Search на category/region.

        CostGuard boundary живёт внутри SearchService:
        cache hit / rate limit → HTTP=0; ALLOWED → ровно 1 HTTP.
        """
        query = _probe_query(category)
        try:
            return await self._search.search_and_store(
                query=query, category=category, region=region,
            )
        except Exception as exc:
            log.warning(
                "CategoryIntelligence: initial_market_probe failed для %r: %s",
                query, exc,
            )
            return []

    async def _collect_fresh(
        self, category: str, region: str, limit: int,
    ) -> None:
        """
        Полный цикл сбора при TTL miss.
        Yandex HTTP: максимум 1 (через initial_market_probe → SearchService).
        """
        # 1. Единый market probe (CostGuard внутри SearchService)
        await self.initial_market_probe(category, region)

        # 2. TrendEngine — из накопленных demand items
        try:
            await self._trend.build_from_demand_items(
                category=category, region=region, save=True,
            )
        except Exception as exc:
            log.warning("CategoryIntelligence: TrendEngine failed: %s", exc)

        # 3. SeasonalityEngine — cross-category (все категории)
        try:
            await self._seasonality.build_from_cross_category(
                region=region, save=True,
            )
        except Exception as exc:
            log.warning("CategoryIntelligence: SeasonalityEngine failed: %s", exc)

        # 4. MarketEventEngine — один pass по probe query (без отдельного HTTP,
        #    если адаптер не подключён — production path в bot.py).
        try:
            await self._market.collect_and_ingest(
                query=_probe_query(category),
                category=category,
                region=region,
                limit=limit,
            )
        except Exception as exc:
            log.warning(
                "CategoryIntelligence: MarketEventEngine failed: %s", exc,
            )

    async def _build_context(
        self,
        category: str,
        region: str,
        limit: int,
        from_cache: bool,
    ) -> CategoryContext:
        """
        Агрегировать данные из store в CategoryContext.
        """
        # demand signals — KnowledgeItem из любого источника для этой категории
        demand_signals = await self._store.search_items(
            category=category,
            region=region,
            limit=limit,
        )

        # trend signals
        trend_signals = await self._store.list_trends(
            category=category,
            region=region,
            limit=limit,
        )

        # seasonal signals — профиль по месяцам
        seasonal_signals = await self._seasonality.get_demand_profile(
            category=category,
            region=region,
        )

        # market events
        all_events = await self._store.list_market_events(
            category=category,
            limit=limit,
        )

        # evidence
        evidence = await self._store.retrieve_evidence(
            category=category,
            min_confidence=_MIN_EVIDENCE_CONFIDENCE,
            limit=limit * 2,
        )

        regulation, platform, competitor = _split_events(all_events)

        confidence = _compute_confidence(
            demand_signals=demand_signals,
            trend_signals=trend_signals,
            seasonal_signals=seasonal_signals,
            evidence=evidence,
            from_cache=from_cache,
        )

        return CategoryContext(
            category=category,
            region=region,
            demand_signals=demand_signals,
            trend_signals=trend_signals,
            seasonal_signals=seasonal_signals,
            market_events=all_events,
            regulation_events=regulation,
            platform_events=platform,
            competitor_events=competitor,
            evidence=evidence,
            confidence=confidence,
            generated_at=time.time(),
            from_cache=from_cache,
        )
