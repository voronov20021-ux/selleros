"""
catalog.py — Intelligence Catalog v1.

Единый каталог поиска всех накопленных Intelligence-данных Argus.

IntelligenceCatalog.search() — единая точка входа для запроса
любых накопленных сигналов: demand, trends, seasonality,
market_events, evidence.

Принципы:
    - Только реальные данные из store/engines.
    - Без HTTP-запросов.
    - Confidence filtering + temporal decay.
    - Дедупликация по fingerprint / ID.
    - Категории и регионы не смешиваются.
    - При отсутствии данных — пустые списки, не ошибка.
"""

from __future__ import annotations

import logging
import re
import statistics
import time
from dataclasses import dataclass, field

from backend.intelligence.evidence.engine import EvidenceEngine
from backend.intelligence.interfaces import IIntelligenceStore
from backend.intelligence.market_events import (
    MarketEventIngestor,
    MIN_RETRIEVE_CONFIDENCE,
    _decay,
)
from backend.intelligence.models import (
    Evidence,
    KnowledgeItem,
    MarketEvent,
    ReviewIssue,
    ReviewSignal,
    SeasonalityRecord,
    TrendRecord,
)
from backend.intelligence.seasonality_engine import SeasonalityEngine
from backend.intelligence.trend_engine import TrendEngine

log = logging.getLogger("selleros.intelligence.catalog")


# ─── snapshot ─────────────────────────────────────────────────────────── #

@dataclass
class IntelligenceSnapshot:
    """
    Компактный срез всех накопленных Intelligence-данных.

    demand        — KnowledgeItem с данными о спросе/рынке
    trends        — TrendRecord направлений
    seasonality   — профиль сезонности {month: demand_index}
    market_events — MarketEvent с temporal decay confidence
    evidence      — Evidence (факты/выводы)
    confidence    — сводный confidence среза (0..1)
    generated_at  — unix timestamp создания среза
    """

    category: str | None
    region: str

    demand: list[KnowledgeItem]       = field(default_factory=list)
    trends: list[TrendRecord]          = field(default_factory=list)
    seasonality: dict[int, float]      = field(default_factory=dict)
    market_events: list[MarketEvent]   = field(default_factory=list)
    evidence: list[Evidence]           = field(default_factory=list)
    reviews: list[ReviewSignal]        = field(default_factory=list)
    review_issues: list[ReviewIssue]   = field(default_factory=list)

    confidence: float                  = 0.0
    generated_at: float                = field(default_factory=time.time)


# ─── IntelligenceCatalog ──────────────────────────────────────────────── #

class IntelligenceCatalog:
    """
    Единый каталог для поиска накопленных Intelligence-данных Argus.

    Не делает HTTP-запросов.
    Работает поверх существующих движков и IntelligenceStore.
    Не изменяет CategoryIntelligence API.

    Использование:

        catalog = IntelligenceCatalog(
            store=store,
            ev_engine=ev_engine,
            trend_engine=trend_engine,
            seasonality_engine=seasonality_engine,
            market_event_ingestor=market_event_ingestor,   # опционально
        )
        snapshot = await catalog.search(category="Часы", region="RU")
    """

    def __init__(
        self,
        store: IIntelligenceStore,
        ev_engine: EvidenceEngine,
        trend_engine: TrendEngine,
        seasonality_engine: SeasonalityEngine,
        market_event_ingestor: MarketEventIngestor | None = None,
    ) -> None:
        self._store      = store
        self._ev         = ev_engine
        self._trends     = trend_engine
        self._seasonality = seasonality_engine
        self._events     = market_event_ingestor

    async def search(
        self,
        category: str | None = None,
        region: str = "RU",
        query: str | None = None,
        days: int = 30,
        min_confidence: float = 0.40,
        limit: int = 20,
        *,
        article: str | None = None,
        user_hash: str | None = None,
    ) -> IntelligenceSnapshot:
        """
        Найти все накопленные Intelligence-данные по заданным фильтрам.

        article / user_hash — опционально для review signals (seller isolation).
        """
        try:
            demand     = await self._fetch_demand(category, region, days, min_confidence, limit, query)
            trends     = await self._fetch_trends(category, region, limit)
            seasonality = await self._fetch_seasonality(category, region)
            market_events = await self._fetch_events(category, region, days, limit)
            evidence   = await self._fetch_evidence(category, min_confidence, limit, query)
            reviews, review_issues = await self._fetch_reviews(
                category=category,
                article=article,
                user_hash=user_hash,
                limit=limit,
            )

            confidence = _compute_snapshot_confidence(
                demand, trends, seasonality, market_events, evidence, review_issues,
            )

            log.info(
                "IntelligenceCatalog.search: cat=%r reg=%s q=%r "
                "demand=%d trends=%d events=%d evidence=%d reviews=%d issues=%d conf=%.2f",
                category, region, query,
                len(demand), len(trends), len(market_events), len(evidence),
                len(reviews), len(review_issues),
                confidence,
            )

            return IntelligenceSnapshot(
                category=category,
                region=region,
                demand=demand,
                trends=trends,
                seasonality=seasonality,
                market_events=market_events,
                evidence=evidence,
                reviews=reviews,
                review_issues=review_issues,
                confidence=confidence,
                generated_at=time.time(),
            )

        except Exception as exc:
            log.error("IntelligenceCatalog.search error: %s", exc, exc_info=True)
            return IntelligenceSnapshot(
                category=category,
                region=region,
                generated_at=time.time(),
            )

    # ─────────────────────── demand ──────────────────────────────────────── #

    async def _fetch_demand(
        self,
        category: str | None,
        region: str,
        days: int,
        min_confidence: float,
        limit: int,
        query: str | None,
    ) -> list[KnowledgeItem]:
        cutoff = (time.time() - days * 86400) if days > 0 else 0.0

        raw = await self._store.search_items(
            category=category,
            region=region,
            limit=limit * 3,  # больше, чтобы пережить фильтрацию
        )

        seen_fps: set[str] = set()
        result: list[KnowledgeItem] = []
        for item in raw:
            # freshness
            if days > 0 and item.collected_at < cutoff:
                continue
            # confidence filter
            if item.confidence < min_confidence:
                continue
            # region: store не всегда фильтрует строго
            if item.region and item.region != region:
                continue
            # category: guard against mixing
            if category and item.category and item.category != category:
                continue
            # query text filter
            if query and not _text_match(item.content or "", query):
                continue
            # dedup by fingerprint
            fp = self._ev.fingerprint(item)
            if fp in seen_fps:
                continue
            seen_fps.add(fp)
            result.append(item)

            if len(result) >= limit:
                break

        # sort by confidence DESC, then collected_at DESC
        result.sort(key=lambda x: (x.confidence, x.collected_at), reverse=True)
        return result[:limit]

    # ─────────────────────── trends ──────────────────────────────────────── #

    async def _fetch_trends(
        self,
        category: str | None,
        region: str,
        limit: int,
    ) -> list[TrendRecord]:
        raw = await self._store.list_trends(
            category=category,
            region=region,
            limit=limit * 2,
        )

        seen: set[str] = set()
        result: list[TrendRecord] = []
        for tr in raw:
            if tr.id in seen:
                continue
            # guard against category mixing
            if category and tr.category and tr.category != category:
                continue
            if tr.region and tr.region != region:
                continue
            seen.add(tr.id)
            result.append(tr)
            if len(result) >= limit:
                break

        result.sort(key=lambda x: x.confidence, reverse=True)
        return result

    # ─────────────────────── seasonality ─────────────────────────────────── #

    async def _fetch_seasonality(
        self,
        category: str | None,
        region: str,
    ) -> dict[int, float]:
        if category is None:
            return {}
        try:
            return await self._seasonality.get_demand_profile(
                category=category,
                region=region,
            )
        except Exception as exc:
            log.warning("IntelligenceCatalog: SeasonalityEngine error: %s", exc)
            return {}

    # ─────────────────────── market events ───────────────────────────────── #

    async def _fetch_events(
        self,
        category: str | None,
        region: str,
        days: int,
        limit: int,
    ) -> list[MarketEvent]:
        if self._events is not None:
            # MarketEventIngestor.retrieve() applies temporal decay + future exclusion
            try:
                raw = await self._events.retrieve(
                    category=category,
                    region=region,
                    days=days if days > 0 else 365,
                )
                return raw[:limit]
            except Exception as exc:
                log.warning("IntelligenceCatalog: MarketEventIngestor.retrieve error: %s", exc)

        # Fallback: query store directly
        cutoff = (time.time() - days * 86400) if days > 0 else 0.0
        raw = await self._store.list_market_events(
            category=category,
            after_ts=cutoff if days > 0 else None,
            limit=limit * 2,
        )

        now = time.time()
        result: list[MarketEvent] = []
        seen: set[str] = set()
        for ev in raw:
            if ev.id in seen:
                continue
            if ev.event_date > now:      # exclude future
                continue
            if category and ev.category and ev.category != category:
                continue
            if ev.region and ev.region != region:
                continue
            age_days = (now - ev.event_date) / 86400
            eff_conf = _decay(ev.confidence, age_days)
            if eff_conf < MIN_RETRIEVE_CONFIDENCE:
                continue
            seen.add(ev.id)
            result.append(ev)
            if len(result) >= limit:
                break

        result.sort(key=lambda e: e.event_date, reverse=True)
        return result

    # ─────────────────────── evidence ────────────────────────────────────── #

    async def _fetch_evidence(
        self,
        category: str | None,
        min_confidence: float,
        limit: int,
        query: str | None,
    ) -> list[Evidence]:
        raw = await self._store.retrieve_evidence(
            category=category,
            min_confidence=min_confidence,
            limit=limit * 3,
        )

        seen: set[str] = set()
        result: list[Evidence] = []
        for ev in raw:
            if ev.id in seen:
                continue
            if query and not _text_match(ev.claim or "", query):
                continue
            seen.add(ev.id)
            result.append(ev)
            if len(result) >= limit:
                break

        result.sort(key=lambda e: e.confidence, reverse=True)
        return result[:limit]

    # ─────────────────────── reviews ─────────────────────────────────────── #

    async def _fetch_reviews(
        self,
        *,
        category: str | None,
        article: str | None,
        user_hash: str | None,
        limit: int,
    ) -> tuple[list[ReviewSignal], list[ReviewIssue]]:
        """Загрузить review signals/issues. Пустые списки при отсутствии данных."""
        if not hasattr(self._store, "search_review_signals"):
            return [], []
        try:
            signals = await self._store.search_review_signals(
                user_hash=user_hash,
                category=category,
                article=article,
                limit=limit,
            )
            issues = await self._store.search_review_issues(
                user_hash=user_hash,
                category=category,
                article=article,
                min_count=1,
                limit=limit,
            )
            return list(signals or []), list(issues or [])
        except Exception as exc:
            log.warning("IntelligenceCatalog: reviews fetch error: %s", exc)
            return [], []


# ─── helpers ──────────────────────────────────────────────────────────── #

def _text_match(text: str, query: str) -> bool:
    """Простой case-insensitive поиск слов query в тексте."""
    text_l = text.lower()
    for word in re.split(r"\s+", query.strip().lower()):
        if word and word in text_l:
            return True
    return False


def _compute_snapshot_confidence(
    demand: list[KnowledgeItem],
    trends: list[TrendRecord],
    seasonality: dict[int, float],
    market_events: list[MarketEvent],
    evidence: list[Evidence],
    review_issues: list | None = None,
) -> float:
    """
    Консервативный сводный confidence для IntelligenceSnapshot.

    +0.10 за каждый непустой тип данных.
    Среднее confidence по evidence, если есть.
    Ограничен 0..0.90.
    """
    score = 0.0
    if demand:
        score += 0.10
    if trends:
        score += 0.10
    if seasonality:
        score += 0.10
    if market_events:
        score += 0.05
    if evidence:
        avg = statistics.mean(e.confidence for e in evidence)
        score += avg * 0.55
    if review_issues:
        score += 0.05

    return round(min(0.90, max(0.0, score)), 4)
