"""
ProductContext — нормализованный снимок товара из нескольких источников.

Поля без данных = None / пустые коллекции. Никогда не подставляем
фиктивные price=0, rating=0 и т.п.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# Поля, которые browser fallback может дозаполнить.
_BROWSER_FILLABLE = ("description", "characteristics", "supplier", "imt_id")


@dataclass
class CompletenessReport:
    """Результат ProductContext.is_complete()."""

    present: list[str]
    missing: list[str]
    needs_browser: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "present": list(self.present),
            "missing": list(self.missing),
            "needs_browser": self.needs_browser,
        }


@dataclass
class ProductIdentity:
    article: int
    imt_id: int | None = None
    root_id: int | None = None
    title: str | None = None
    brand: str | None = None
    supplier: str | None = None


@dataclass
class ProductPricing:
    price: int | None = None
    old_price: int | None = None
    rating: float | None = None
    feedback_count: int | None = None


@dataclass
class ProductMedia:
    photos: list[str] = field(default_factory=list)
    photo_count: int | None = None


@dataclass
class ProductDescription:
    description: str | None = None
    characteristics: dict[str, Any] = field(default_factory=dict)
    sizes: list[Any] = field(default_factory=list)


@dataclass
class ProductReviews:
    reviews_count: int | None = None
    review_texts: list[str] = field(default_factory=list)
    review_summary: str | None = None


@dataclass
class ProductContext:
    """Единый контекст товара для Argus (независимо от источника полей)."""

    product: ProductIdentity
    pricing: ProductPricing = field(default_factory=ProductPricing)
    media: ProductMedia = field(default_factory=ProductMedia)
    description: ProductDescription = field(default_factory=ProductDescription)
    reviews: ProductReviews = field(default_factory=ProductReviews)
    #: field_name → source label, e.g. {"price": "search_api", "photos": "cdn"}
    sources: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------ helpers

    def _field_map(self) -> dict[str, Any]:
        """Плоская карта проверяемых полей → значение."""
        photo_count = self.media.photo_count
        if photo_count is None and self.media.photos:
            photo_count = len(self.media.photos)
        return {
            "article": self.product.article,
            "imt_id": self.product.imt_id,
            "root_id": self.product.root_id,
            "title": self.product.title,
            "brand": self.product.brand,
            "supplier": self.product.supplier,
            "price": self.pricing.price,
            "old_price": self.pricing.old_price,
            "rating": self.pricing.rating,
            "feedback_count": self.pricing.feedback_count,
            "photos": self.media.photos,
            "photo_count": photo_count,
            "description": self.description.description,
            "characteristics": self.description.characteristics,
            "sizes": self.description.sizes,
            "reviews_count": self.reviews.reviews_count,
            "review_texts": self.reviews.review_texts,
            "review_summary": self.reviews.review_summary,
        }

    @staticmethod
    def _is_present(name: str, value: Any) -> bool:
        if value is None:
            return False
        if name == "article":
            try:
                return int(value) > 0
            except (TypeError, ValueError):
                return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (list, dict, tuple, set)):
            return len(value) > 0
        if isinstance(value, bool):
            return True
        # int/float: 0 допустим только если это реальный счётчик с источника;
        # для price/rating мы никогда не пишем 0 «от себя», но 0 отзывов
        # с API — валидное значение → считаем present.
        return True

    def is_complete(self) -> CompletenessReport:
        present: list[str] = []
        missing: list[str] = []
        for name, value in self._field_map().items():
            if self._is_present(name, value):
                present.append(name)
            else:
                missing.append(name)

        needs_browser = any(name in missing for name in _BROWSER_FILLABLE)

        return CompletenessReport(
            present=present,
            missing=missing,
            needs_browser=needs_browser,
        )

    def to_prompt(self) -> str:
        """Русский структурированный текст для Argus (без выдуманных полей)."""
        p = self.product
        pr = self.pricing
        m = self.media
        d = self.description
        r = self.reviews

        lines: list[str] = [
            "=== КОНТЕКСТ ТОВАРА (ProductContext) ===",
            "",
            "## Идентификация",
            f"Артикул: {p.article}",
        ]
        if p.imt_id is not None:
            lines.append(f"IMT ID: {p.imt_id}")
        if p.root_id is not None:
            lines.append(f"Root ID: {p.root_id}")
        if p.title:
            lines.append(f"Название: {p.title}")
        if p.brand:
            lines.append(f"Бренд: {p.brand}")
        if p.supplier:
            lines.append(f"Продавец: {p.supplier}")

        lines.append("")
        lines.append("## Цена и рейтинг")
        if pr.price is not None:
            price_line = f"Цена: {pr.price} руб."
            if pr.old_price is not None and pr.old_price > pr.price:
                price_line += f" (было {pr.old_price} руб.)"
            lines.append(price_line)
        else:
            lines.append("Цена: нет данных")
        if pr.rating is not None:
            lines.append(f"Рейтинг: {pr.rating}")
        else:
            lines.append("Рейтинг: нет данных")
        if pr.feedback_count is not None:
            lines.append(f"Отзывов на карточке: {pr.feedback_count}")
        else:
            lines.append("Отзывов на карточке: нет данных")

        lines.append("")
        lines.append("## Медиа")
        photo_n = m.photo_count if m.photo_count is not None else len(m.photos)
        lines.append(f"Фотографий: {photo_n}")
        for url in (m.photos or [])[:5]:
            lines.append(f"- {url}")

        lines.append("")
        lines.append("## Описание")
        if d.description and d.description.strip():
            preview = d.description.strip()
            if len(preview) > 800:
                preview = preview[:800] + "…"
            lines.append(preview)
        else:
            lines.append("(описание отсутствует)")

        lines.append("")
        lines.append("## Характеристики")
        chars = d.characteristics or {}
        if chars:
            for key, value in list(chars.items())[:20]:
                lines.append(f"- {key}: {value}")
            if len(chars) > 20:
                lines.append(f"… ещё {len(chars) - 20}")
        else:
            lines.append("(характеристики отсутствуют)")

        if d.sizes:
            lines.append("")
            lines.append("## Размеры")
            for size in d.sizes[:12]:
                if isinstance(size, dict):
                    name = size.get("name") or size.get("orig_name") or ""
                    qty = size.get("qty")
                    price = size.get("price")
                    bit = name or str(size)
                    if price is not None:
                        bit += f", цена {price}"
                    if qty is not None:
                        bit += f", остаток {qty}"
                    lines.append(f"- {bit}")
                else:
                    name = getattr(size, "name", None) or getattr(size, "orig_name", "") or ""
                    qty = getattr(size, "qty", None)
                    price = getattr(size, "price", None)
                    bit = str(name) if name else str(size)
                    if price is not None:
                        bit += f", цена {price}"
                    if qty is not None:
                        bit += f", остаток {qty}"
                    lines.append(f"- {bit}")

        lines.append("")
        lines.append("## Отзывы")
        if r.reviews_count is not None:
            lines.append(f"Текстов отзывов: {r.reviews_count}")
        elif r.review_texts:
            lines.append(f"Текстов отзывов: {len(r.review_texts)}")
        else:
            lines.append("Текстов отзывов: нет данных")
        if r.review_summary:
            lines.append(f"Сводка: {r.review_summary}")
        for text in (r.review_texts or [])[:8]:
            snippet = (text or "").replace("\n", " ").strip()
            if len(snippet) > 220:
                snippet = snippet[:220] + "…"
            if snippet:
                lines.append(f"- {snippet}")

        if self.sources:
            lines.append("")
            lines.append("## Источники полей")
            for key in sorted(self.sources):
                lines.append(f"- {key}: {self.sources[key]}")

        report = self.is_complete()
        lines.append("")
        lines.append("## Полнота")
        lines.append(f"Есть: {', '.join(report.present) or '—'}")
        lines.append(f"Нет: {', '.join(report.missing) or '—'}")
        lines.append(f"Нужен browser: {'да' if report.needs_browser else 'нет'}")

        return "\n".join(lines)
