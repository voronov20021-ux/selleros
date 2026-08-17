"""
ProductCompetitorProfile — только из карточки. Никаких выдуманных атрибутов.
"""

from __future__ import annotations

import re
from typing import Any

from backend.competitor_intelligence.models import ProductCompetitorProfile

_STOPWORDS = frozenset({
    "для", "и", "или", "на", "в", "с", "со", "по", "от", "из", "к", "ко",
    "а", "но", "the", "and", "or", "of", "to", "a", "an", "шт", "см", "мм",
    "набор", "комплект", "новый", "новая", "новое", "мужской", "женский",
})

_TYPE_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("кроссовки", ("кроссов", "sneaker")),
    ("кеды", ("кед",)),
    ("ботинки", ("ботин",)),
    ("туфли", ("туфл",)),
    ("куртка", ("куртк", "бомбер", "ветровк")),
    ("футболка", ("футболк", "t-shirt", "tee")),
    ("часы", ("час", "watch")),
    ("очки", ("очк", "glasses")),
    ("наушники", ("наушник", "headset", "earbuds")),
    ("чехол", ("чехол", "case")),
)


def _tokens(text: str | None) -> list[str]:
    if not text:
        return []
    parts = re.findall(r"[A-Za-zА-Яа-яЁё0-9]+", str(text).lower().replace("ё", "е"))
    out: list[str] = []
    for tok in parts:
        if len(tok) < 2:
            continue
        if tok in _STOPWORDS:
            continue
        if tok.isdigit() and len(tok) < 3:
            continue
        out.append(tok)
    return out


def _infer_product_type(*, title: str | None, category: str | None) -> str | None:
    blob = f"{title or ''} {category or ''}".lower().replace("ё", "е")
    if not blob.strip():
        return None
    for label, keys in _TYPE_HINTS:
        if any(k in blob for k in keys):
            return label
    if category and str(category).strip():
        return str(category).strip()
    return None


def _keywords_from_card(
    *,
    title: str | None,
    brand: str | None,
    category: str | None,
    chars: dict[str, Any],
    max_words: int = 8,
) -> list[str]:
    seen: set[str] = set()
    words: list[str] = []

    def add(tok: str | None) -> None:
        t = (tok or "").strip().lower().replace("ё", "е")
        if not t or t in seen or t in _STOPWORDS:
            return
        if t.isdigit() and len(t) < 3:
            return
        seen.add(t)
        words.append(t)

    for tok in _tokens(title):
        add(tok)
        if len(words) >= max_words:
            return words
    add(brand.lower() if brand else None)
    for tok in _tokens(category):
        add(tok)
    for key, val in (chars or {}).items():
        for tok in _tokens(str(val))[:2]:
            add(tok)
        if len(words) >= max_words:
            break
    return words[:max_words]


def _positioning(
    *,
    brand: str | None,
    product_type: str | None,
    category: str | None,
    price: int | None,
    title: str | None,
) -> str | None:
    bits: list[str] = []
    if brand:
        bits.append(str(brand).strip())
    if product_type:
        bits.append(product_type)
    elif category:
        bits.append(str(category).strip())
    elif title:
        bits.append(str(title).strip()[:60])
    if price is not None:
        bits.append(f"{int(price)} ₽")
    return " / ".join(bits) if bits else None


def _photo_count(product: Any) -> int | None:
    pc = getattr(product, "photo_count", None)
    if pc is not None:
        try:
            n = int(pc)
            return n if n >= 0 else None
        except (TypeError, ValueError):
            pass
    photos = getattr(product, "photos", None) or []
    if isinstance(photos, int):
        return photos if photos >= 0 else None
    if photos:
        return len(photos)
    return None


def build_competitor_profile(product: Any) -> ProductCompetitorProfile:
    """Собрать профиль поиска строго из известных полей карточки."""
    if product is None:
        return ProductCompetitorProfile()

    title = getattr(product, "title", None) or None
    if isinstance(title, str):
        title = title.strip() or None
    brand = getattr(product, "brand", None) or None
    if isinstance(brand, str):
        brand = brand.strip() or None
    category = getattr(product, "subject_name", None) or getattr(product, "category_name", None)
    if isinstance(category, str):
        category = category.strip() or None
    subcategory = getattr(product, "subject_root_name", None)
    if isinstance(subcategory, str):
        subcategory = subcategory.strip() or None

    chars = getattr(product, "characteristics", None) or {}
    if not isinstance(chars, dict):
        chars = {}
    chars = {str(k): v for k, v in chars.items() if k is not None}

    price = getattr(product, "price", None)
    try:
        price = int(price) if price is not None else None
    except (TypeError, ValueError):
        price = None

    rating = getattr(product, "rating", None)
    try:
        rating = float(rating) if rating is not None else None
    except (TypeError, ValueError):
        rating = None

    feedbacks = getattr(product, "feedbacks", None)
    try:
        feedbacks = int(feedbacks) if feedbacks is not None else None
    except (TypeError, ValueError):
        feedbacks = None

    article = getattr(product, "article", None)
    try:
        article = int(article) if article is not None else None
    except (TypeError, ValueError):
        article = None

    product_type = _infer_product_type(title=title, category=category)
    keywords = _keywords_from_card(
        title=title, brand=brand, category=category, chars=chars,
    )
    char_count = len(chars) if chars else None

    return ProductCompetitorProfile(
        article=article,
        title=title,
        brand=brand,
        category=category,
        subcategory=subcategory,
        key_characteristics=chars,
        price=price,
        rating=rating,
        feedbacks=feedbacks,
        photo_count=_photo_count(product),
        keywords=keywords,
        product_type=product_type,
        positioning=_positioning(
            brand=brand,
            product_type=product_type,
            category=category,
            price=price,
            title=title,
        ),
        char_count=char_count,
    )


def build_search_query(profile: ProductCompetitorProfile) -> str:
    """Один детерминированный query. Не плодит дубликаты."""
    parts: list[str] = []
    if profile.keywords:
        parts.append(" ".join(profile.keywords[:5]))
    elif profile.title:
        parts.append(str(profile.title)[:80])
    blob = " ".join(parts).lower()
    if profile.category and profile.category.lower() not in blob:
        parts.append(profile.category)
    q = " ".join(p for p in parts if p).strip()
    if not q:
        return "site:wildberries.ru"
    return f"site:wildberries.ru {q}"
