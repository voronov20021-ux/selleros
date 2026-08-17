"""
models.py — форма данных долговременной памяти ARGUS.

Это просто описания «что мы помним» (dataclass), без логики хранения.
Как именно хранить — решает store.py. Модели не знают про SQLite,
поэтому смена на PostgreSQL их не коснётся вообще.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DialogMessage:
    """Одно сообщение диалога — от продавца или от ARGUS."""
    id: int | None
    user_id: int
    role: str          # "user" | "assistant"
    content: str
    created_at: float


@dataclass
class AnalysisRecord:
    """Один разбор карточки — запись в истории анализов."""
    id: int | None
    user_id: int
    article: int
    marketplace: str
    title: str
    price: int | None
    score: int
    verdict: str
    created_at: float


@dataclass
class ProductRecord:
    """
    Товар, за которым продавец следит.

    Одна строка на пару (продавец, артикул) — не растёт с каждым
    повторным анализом. Обновляется каждый раз, когда товар
    разбирают заново; старые значения полей уходят в ProductChange.
    """
    user_id: int
    article: int
    marketplace: str
    title: str
    price: int | None
    rating: float | None
    score: int | None
    photos: int
    first_seen: float
    last_seen: float

    # --- данные продавца (SellerData) -----------------------------------
    # Добавлены поверх исходной формы записи — все с безопасными
    # дефолтами, поэтому старый код, создающий ProductRecord с прежним
    # набором аргументов, не ломается.
    feedbacks: int | None = None
    price_source: str | None = None
    rating_source: str | None = None
    feedbacks_source: str | None = None
    sales: int | None = None
    orders: int | None = None
    period: str | None = None
    seller_updated_at: float | None = None
    # Private metrics (optional)
    ctr: float | None = None
    cvr: float | None = None
    returns: int | None = None
    ad_spend: float | None = None
    cost: float | None = None
    commission: float | None = None
    logistics: float | None = None
    storage: float | None = None
    impressions: int | None = None
    views: int | None = None
    # WB group ids для reviews (feedbacks*.wb.ru); None не затирает при upsert.
    imt_id: int | None = None
    root_id: int | None = None


@dataclass
class ProductChange:
    """
    Одно изменение одного поля товара.

    Универсальный журнал: подходит и для цены, и для фото,
    и для рейтинга — не нужно заводить отдельную таблицу на каждое поле.
    «История цены» — это просто фильтр field == "price".
    """
    id: int | None
    user_id: int
    article: int
    field: str          # "price" | "rating" | "score" | "photos"
    old_value: str | None
    new_value: str | None
    changed_at: float


@dataclass
class Recommendation:
    """Одна рекомендация ARGUS по конкретному товару."""
    id: int | None
    user_id: int
    article: int
    text: str
    status: str         # "pending" | "done" | "dismissed"
    created_at: float
    completed_at: float | None


@dataclass
class ProductDecision:
    """
    Структурированное продуктовое решение продавца (seller-isolated).

    Legacy-совместимая форма. Полная модель v1 — DecisionRecord
    в backend.intelligence.solution_research (и MemoryStore API).
    """
    id: int | None
    user_id: int
    article: int
    topic: str
    problem: str = ""
    evidence: str = ""
    recommendation: str = ""
    seller_question: str = ""
    solution_options: str = ""   # JSON / compact text
    seller_choice: str | None = None
    action: str | None = None
    outcome: str | None = None
    created_at: float = 0.0
    updated_at: float = 0.0
    # Decision Memory v1 fields (optional / migrated)
    problem_id: str | None = None
    selected_solution_id: str | None = None
    seller_comment: str | None = None
    status: str = "PROPOSED"
    outcome_tracker_id: str | None = None


# Alias used by Solution Research + Decision Memory v1 docs/tests
DecisionRecordLegacy = ProductDecision


@dataclass
class ProductMetricSnapshot:
    """
    Точечный снимок метрик товара во времени (Dynamic Analytics).

    Не смешивает артикулы / периоды / источники.
    Card feedbacks ≠ seller processed metrics — provenance per field.
    """
    id: int | None
    user_id: int
    article: int
    marketplace: str
    captured_at: float
    period: str | None = None
    price: float | None = None
    rating: float | None = None
    feedbacks: int | None = None
    impressions: int | None = None
    views: int | None = None
    clicks: int | None = None
    ctr: float | None = None
    orders: int | None = None
    sales: int | None = None
    cvr: float | None = None
    revenue: float | None = None
    costs: float | None = None
    profit: float | None = None
    margin: float | None = None
    stock: int | None = None
    ad_spend: float | None = None
    cost: float | None = None
    returns: int | None = None
    source: str | None = None          # user|api|computed|session|card
    confidence: float | None = None
    provenance: str | None = None      # JSON dict field→source