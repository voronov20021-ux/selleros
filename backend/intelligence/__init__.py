"""
backend/intelligence — Intelligence Layer для Argus.

Публичное API модуля:

    IntelligenceStore     — SQLite-реализация хранилища
    IIntelligenceStore    — абстрактный контракт (для type hints и тестов)
    EvidenceEngine        — ingestion + retrieval знаний
    TrendEngine           — построение TrendRecord из накопленных данных
    SeasonalityEngine     — построение SeasonalityRecord из накопленных данных
    SourceRegistry        — реестр адаптеров источников
    WordstatAdapter       — адаптер Yandex Wordstat (Search API proxy)
    YandexSearchAdapter   — адаптер Yandex Search API (рабочая реализация)
    SearchService         — оркестратор: fetch + save + ingest

Модели:
    DataSource, KnowledgeItem, Evidence,
    SellerObservation, SeasonalityRecord, TrendRecord, MarketEvent

Быстрый старт (SearchService):

    from backend.intelligence import (
        IntelligenceStore, EvidenceEngine, SearchService
    )
    store = IntelligenceStore("data/intelligence.db")
    await store.connect()
    engine = EvidenceEngine(store=store)
    svc = SearchService(store=store, engine=engine)
    await svc.ensure_source_registered()
    items = await svc.search_and_store(
        query="мужские часы",
        category="Часы",
    )
"""

from backend.intelligence.event_sources.base import EventSourceAdapter
from backend.intelligence.event_sources.yandex_news import YandexNewsAdapter
from backend.intelligence.evidence.aggregator import AggregatedEvidence, EvidenceAggregator
from backend.intelligence.market_event_engine import MarketEventEngine
from backend.intelligence.seasonality_engine import SeasonalityEngine
from backend.intelligence.trend_engine import TrendEngine
from backend.intelligence.evidence.category import CategoryResolver
from backend.intelligence.evidence.conflicts import (
    ConflictDetector,
    ConflictSeverity,
    EvidenceConflict,
)
from backend.intelligence.evidence.engine import EvidenceEngine
from backend.intelligence.evidence.signals import SignalExtractor, SignalType
from backend.intelligence.interfaces import IIntelligenceStore
from backend.intelligence.models import (
    DataSource,
    Evidence,
    EvidenceType,
    ItemType,
    KnowledgeItem,
    MarketEvent,
    SeasonalityRecord,
    SellerObservation,
    SourceType,
    TrendRecord,
)
from backend.intelligence.search_service import SearchService
from backend.intelligence.sources.registry import SourceRegistry
from backend.intelligence.sources.wordstat import WordstatAdapter
from backend.intelligence.sources.yandex_search import YandexSearchAdapter
from backend.intelligence.store import IntelligenceStore

__all__ = [
    # store
    "IntelligenceStore",
    "IIntelligenceStore",
    # engine
    "EvidenceEngine",
    "TrendEngine",
    "SeasonalityEngine",
    "MarketEventEngine",
    # event sources
    "EventSourceAdapter",
    "YandexNewsAdapter",
    # sources
    "SourceRegistry",
    "WordstatAdapter",
    "YandexSearchAdapter",
    # service
    "SearchService",
    # evidence v2
    "SignalExtractor",
    "SignalType",
    "CategoryResolver",
    "ConflictDetector",
    "EvidenceConflict",
    "ConflictSeverity",
    "EvidenceAggregator",
    "AggregatedEvidence",
    # models
    "DataSource",
    "KnowledgeItem",
    "Evidence",
    "EvidenceType",
    "ItemType",
    "SourceType",
    "SellerObservation",
    "SeasonalityRecord",
    "TrendRecord",
    "MarketEvent",
]
