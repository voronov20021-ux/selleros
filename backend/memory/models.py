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
