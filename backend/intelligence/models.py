"""
models.py — доменные модели Intelligence Layer.

Чистые dataclass-ы без зависимостей от хранилища или транспорта.
Смена SQLite → PostgreSQL не требует правок в этом файле.

Иерархия сущностей
─────────────────────────────────────────────────────────
DataSource          — зарегистрированный источник данных
KnowledgeItem       — сырая запись из источника
Evidence            — обработанный, типизированный факт/наблюдение/вывод
SellerObservation   — обезличенное наблюдение продавца (cause → effect)
SeasonalityRecord   — сезонный индекс спроса (категория / месяц / регион)
TrendRecord         — направление динамики запроса или категории
MarketEvent         — внешнее событие, влияющее на рынок
─────────────────────────────────────────────────────────

Разграничение типов данных (строгое):

    EvidenceType.FACT           верифицируемо из источника
                                "Wordstat: запрос 'мужские часы' — 150 000
                                 показов, январь 2026"

    EvidenceType.OBSERVATION    измеренный результат действия
                                "добавили инфографику → +18% заказов за 30 дней"

    EvidenceType.INFERENCE      вывод из нескольких фактов / наблюдений
                                "спрос на мужские часы растёт в ноябре–декабре"

Предположения явно помечаются как INFERENCE — никогда как FACT.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


# ──────────────────────────────────────────────────────── перечисления ──── #


class SourceType(str, Enum):
    """Природа источника данных."""

    PUBLIC_API      = "public_api"       # открытый API (Wordstat, МойСклад, …)
    SCRAPED         = "scraped"          # парсинг веб-страниц
    OFFICIAL        = "official"         # официальные данные (WB-документы, ФНС)
    USER_GENERATED  = "user_generated"   # обезличенные наблюдения продавцов
    MANUAL          = "manual"           # вручную внесено командой Argus


class ItemType(str, Enum):
    """Тип сырой записи при ingestion."""

    FACT            = "fact"
    OBSERVATION     = "observation"
    INFERENCE       = "inference"
    RECOMMENDATION  = "recommendation"


class EvidenceType(str, Enum):
    """Тип обработанной единицы знания.

    Строго следует таксономии: факт → наблюдение → вывод.
    Нельзя выдавать вывод за факт.
    """

    FACT        = "fact"
    OBSERVATION = "observation"
    INFERENCE   = "inference"


class TrendDirection(str, Enum):
    UP      = "up"
    DOWN    = "down"
    STABLE  = "stable"


class ImpactDirection(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL  = "neutral"


class ChangeType(str, Enum):
    """Тип изменения, зафиксированного в SellerObservation."""

    PRICE       = "price"
    CONTENT     = "content"   # фото, описание, характеристики
    AD          = "ad"        # реклама (ставка, тип)
    RANKING     = "ranking"   # позиция в выдаче
    OTHER       = "other"


class EventType(str, Enum):
    SALE        = "sale"        # акция WB или конкурента
    HOLIDAY     = "holiday"     # праздник / сезон
    REGULATION  = "regulation"  # изменение правил площадки или законодательства
    COMPETITOR  = "competitor"  # действие конкурента
    PLATFORM    = "platform"    # изменения на самой платформе WB
    ECONOMIC    = "economic"    # макроэкономическое событие (курс, инфляция, санкции)


# ───────────────────────────────────────────────────────── модели ──────── #


@dataclass
class DataSource:
    """
    Зарегистрированный источник данных.

    authority — насколько доверяем источнику (0.0 = не доверяем, 1.0 = полностью).
    freshness_hours — через сколько часов данные считаются устаревшими.
    capabilities — список строк-идентификаторов того, что источник умеет,
        например: ["search_demand", "query_dynamics", "regional_demand"].
    """

    id: str
    name: str
    source_type: SourceType
    authority: float                     # 0.0 – 1.0
    freshness_hours: int                 # часов до устаревания
    capabilities: list[str]             # строки-ключи возможностей
    base_url: str | None = None
    is_active: bool = True
    last_fetched_at: float | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class KnowledgeItem:
    """
    Сырая запись, полученная от внешнего источника.

    Это «необработанные данные» — именно они поступают из
    DataSourceAdapter.fetch() и хранятся в knowledge_items.
    EvidenceEngine превращает их в Evidence с явным типом и confidence.

    metadata — структурированные поля, специфичные для источника:
        для Wordstat: {"query": "мужские часы", "impressions": 150000,
                       "region_id": 225, "related_queries": [...]}
        для market_event: {"url": "...", "author": "..."}
    """

    id: str
    source_id: str
    collected_at: float
    item_type: ItemType
    content: str                         # человекочитаемое описание
    confidence: float = 1.0             # 0.0 – 1.0
    source_url: str | None = None
    published_at: float | None = None
    category: str | None = None         # категория WB ("Часы", "Одежда", …)
    region: str | None = None           # "RU", "MSK", …
    period: str | None = None           # "2026-01", "Q1-2026", …
    metadata: dict = field(default_factory=dict)


@dataclass
class Evidence:
    """
    Обработанная, нормализованная единица знания.

    claim — короткое нормализованное утверждение, пригодное для
        включения в промпт Argus.
    evidence_type — строгое разграничение: факт, наблюдение, вывод.
    supporting_data — JSON-совместимый dict со ссылками на источники
        и числами, подтверждающими claim.

    Confidence decay: свежий FACT из authoritative источника → 1.0;
    старый INFERENCE из низкоавторитетного → может опускаться до 0.1.
    EvidenceEngine.decay_confidence() применяет этот расчёт при retrieval.
    """

    id: str
    knowledge_item_id: str
    evidence_type: EvidenceType
    claim: str
    created_at: float
    confidence: float = 1.0
    supporting_data: dict = field(default_factory=dict)


@dataclass
class SellerObservation:
    """
    Обезличенное наблюдение продавца: действие → измеренный результат.

    user_hash — sha256(str(user_id)).  raw user_id НИКОГДА не хранится.
    article — артикул WB (публичные данные, не персональные).

    Пример:
        change_type=CONTENT
        before_value="3 фото"
        after_value="12 фото, инфографика"
        period_start=...  period_end=30 дней спустя
        outcome_orders_delta=+47
    """

    id: str
    user_hash: str                       # sha256, не raw user_id
    article: int
    created_at: float
    change_type: ChangeType
    category: str | None = None
    before_value: str | None = None
    after_value: str | None = None
    period_start: float | None = None
    period_end: float | None = None
    outcome_sales_delta: int | None = None
    outcome_orders_delta: int | None = None
    outcome_rating_delta: float | None = None
    notes: str | None = None


@dataclass
class SeasonalityRecord:
    """
    Сезонный индекс спроса для категории.

    demand_index — относительный показатель спроса:
        1.0 = среднегодовой уровень,
        1.4 = на 40% выше среднего,
        0.6 = на 40% ниже среднего.

    Источник может быть Wordstat (исторические данные по запросам)
    или внутренним наблюдением (seller_observations).
    """

    id: str
    category: str
    region: str
    month: int                          # 1 – 12
    demand_index: float
    source_id: str
    period_year: int
    created_at: float
    week: int | None = None             # 1 – 53, если есть недельная детализация
    confidence: float = 1.0


@dataclass
class TrendRecord:
    """
    Направление динамики поискового запроса или категории за период.

    change_pct — процент изменения (None если неизвестно точное число).
    query — конкретный поисковый запрос (из Wordstat);
            None если запись относится к целой категории.
    """

    id: str
    source_id: str
    period_start: float
    period_end: float
    direction: TrendDirection
    created_at: float
    category: str | None = None
    query: str | None = None
    region: str | None = None
    change_pct: float | None = None
    confidence: float = 1.0
    metadata: dict = field(default_factory=dict)


@dataclass
class MarketEvent:
    """
    Внешнее событие, влияющее на рыночную динамику.

    Примеры:
        - «Большая распродажа WB, ноябрь 2026»  (event_type=SALE)
        - «Изменение алгоритма поиска WB»        (event_type=PLATFORM)
        - «Новые требования к сертификации»       (event_type=REGULATION)

    impact_direction — общее влияние на категорию:
        POSITIVE (рост продаж), NEGATIVE (падение), NEUTRAL или None (неизвестно).
    """

    id: str
    event_type: EventType
    title: str
    source_id: str
    event_date: float
    created_at: float
    description: str | None = None
    category: str | None = None
    region: str | None = None
    impact_direction: ImpactDirection | None = None
    confidence: float = 1.0
    metadata: dict = field(default_factory=dict)


# ────────────────────────────────────────────────── review intelligence ── #


class ReviewSignalType(str, Enum):
    """Категории Review Intelligence v2 (расширяемые). Старые алиасы сохранены."""

    # v2 primary
    PACKAGING          = "PACKAGING"
    UNPACKING          = "UNPACKING"
    COMPLETENESS       = "COMPLETENESS"
    PRODUCT_QUALITY    = "PRODUCT_QUALITY"
    FUNCTIONALITY      = "FUNCTIONALITY"
    PHOTO_MATCH        = "PHOTO_MATCH"
    DESCRIPTION_MATCH  = "DESCRIPTION_MATCH"
    SIZE               = "SIZE"
    DESIGN             = "DESIGN"
    LOGISTICS          = "LOGISTICS"       # только если сигнал из отзывов
    EXPECTATIONS       = "EXPECTATIONS"
    # back-compat / adjacent
    QUALITY            = "QUALITY"         # → prefer PRODUCT_QUALITY
    DAMAGE             = "DAMAGE"
    DELIVERY           = "DELIVERY"        # → prefer LOGISTICS
    APPEARANCE         = "APPEARANCE"      # → prefer PHOTO_MATCH / DESIGN
    PRICE_VALUE        = "PRICE_VALUE"
    SERVICE            = "SERVICE"
    OTHER              = "OTHER"


class ReviewSentiment(str, Enum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    NEUTRAL  = "NEUTRAL"
    UNKNOWN  = "UNKNOWN"


class SignalSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH     = "high"
    MEDIUM   = "medium"
    LOW      = "low"


@dataclass
class ReviewSignal:
    """Один нормализованный сигнал из отзыва."""

    id: str
    category: str | None
    signal_type: ReviewSignalType
    sentiment: ReviewSentiment
    claim: str
    confidence: float
    source_ids: list[str] = field(default_factory=list)
    user_hash: str | None = None
    article: str | None = None
    source_url: str | None = None
    review_id: str | None = None
    created_at: float = 0.0
    metadata: dict = field(default_factory=dict)


@dataclass
class ReviewIssue:
    """Группа похожих отзывов (recurring issue / strength)."""

    id: str
    category: str | None
    signal_type: ReviewSignalType
    sentiment: ReviewSentiment
    claim: str
    count: int
    ratio: float
    confidence: float
    source_ids: list[str] = field(default_factory=list)
    user_hash: str | None = None
    article: str | None = None
    created_at: float = 0.0
    metadata: dict = field(default_factory=dict)


class SignalFrequency(str, Enum):
    HIGH   = "HIGH"
    MEDIUM = "MEDIUM"
    LOW    = "LOW"


class ProblemDirection(str, Enum):
    NEGATIVE = "negative"
    POSITIVE = "positive"
    MIXED    = "mixed"


@dataclass
class SellerProblem:
    """Нормализованная проблема/сильная сторона для продавца (не raw отзыв)."""

    id: str
    label: str
    frequency: SignalFrequency
    confidence: float
    direction: ProblemDirection
    priority: int  # 1=P1 … 4=P4
    signal_type: ReviewSignalType
    evidence_ids: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    claim: str = ""
    count: int = 0
    severity: SignalSeverity = SignalSeverity.LOW
    rationale: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class SellerAction:
    """Конкретное действие продавца на основе review-сигнала."""

    id: str
    title: str
    rationale: str
    confidence: float
    priority: int  # 1=P1 … 4=P4
    evidence_ids: list[str] = field(default_factory=list)
    problem_id: str | None = None
    signal_type: ReviewSignalType | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class ReviewAssessment:
    """Сводный результат ReviewIntelligence по товару/категории."""

    category: str | None
    article: str | None = None
    user_hash: str | None = None
    processed_count: int = 0
    signals: list[ReviewSignal] = field(default_factory=list)
    issues: list[ReviewIssue] = field(default_factory=list)
    problems: list[SellerProblem] = field(default_factory=list)
    actions: list[SellerAction] = field(default_factory=list)
    confidence: float = 0.0
    generated_at: float = 0.0
    metadata: dict = field(default_factory=dict)
