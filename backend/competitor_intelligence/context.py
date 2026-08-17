"""
CompetitorContext + оркестратор сборки для Argus.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from backend.competitor_intelligence.analyzer import CompetitorAnalyzer
from backend.competitor_intelligence.collector import (
    CompetitorCollector,
    await_wb_rate_slot,
)
from backend.competitor_intelligence.matcher import CompetitorMatcher
from backend.competitor_intelligence.models import (
    CompetitorAnalysis,
    CompetitorCandidate,
    CompetitorProduct,
    MainProductSnapshot,
)

log = logging.getLogger("selleros.competitor_intelligence.context")


def main_from_product_context(
    product_context: Any,
    *,
    category_id: int | None = None,
    category_name: str | None = None,
) -> MainProductSnapshot:
    product = getattr(product_context, "product", None)
    pricing = getattr(product_context, "pricing", None)
    media = getattr(product_context, "media", None)
    description = getattr(product_context, "description", None)

    photo_count = getattr(media, "photo_count", None) if media else None
    if photo_count is None and media is not None:
        photos = getattr(media, "photos", None) or []
        if photos:
            photo_count = len(photos)

    desc = getattr(description, "description", None) if description else None
    has_desc = bool(desc and str(desc).strip()) if desc is not None else None
    if desc is None:
        has_desc = False

    return MainProductSnapshot(
        article=int(getattr(product, "article")),
        title=getattr(product, "title", None) if product else None,
        price=getattr(pricing, "price", None) if pricing else None,
        rating=getattr(pricing, "rating", None) if pricing else None,
        feedbacks=getattr(pricing, "feedback_count", None) if pricing else None,
        category_id=category_id or getattr(product_context, "category_id", None),
        category_name=category_name or getattr(product_context, "category_name", None),
        brand=getattr(product, "brand", None) if product else None,
        photo_count=photo_count,
        has_description=has_desc,
    )


def candidate_to_product(cand: CompetitorCandidate) -> CompetitorProduct:
    return CompetitorProduct(
        article=cand.article,
        title=cand.title,
        price=cand.price,
        rating=cand.rating,
        feedbacks=cand.feedbacks,
        photos=[],
        photo_count=cand.photo_count,
        description=None,
        brand=cand.brand,
        category_id=cand.category_id,
        category_name=cand.category_name,
        match_score=cand.score,
        sources={"commercial": "search_api"},
    )


def product_context_to_competitor(
    ctx: Any,
    *,
    match_score: float | None = None,
    fallback: CompetitorCandidate | None = None,
) -> CompetitorProduct:
    product = getattr(ctx, "product", None)
    pricing = getattr(ctx, "pricing", None)
    media = getattr(ctx, "media", None)
    description = getattr(ctx, "description", None)
    sources = dict(getattr(ctx, "sources", None) or {})

    photos = list(getattr(media, "photos", None) or []) if media else []
    photo_count = getattr(media, "photo_count", None) if media else None
    if photo_count is None and photos:
        photo_count = len(photos)

    article = int(getattr(product, "article"))
    title = getattr(product, "title", None) if product else None
    price = getattr(pricing, "price", None) if pricing else None
    rating = getattr(pricing, "rating", None) if pricing else None
    feedbacks = getattr(pricing, "feedback_count", None) if pricing else None
    brand = getattr(product, "brand", None) if product else None
    desc = getattr(description, "description", None) if description else None

    # Не затираем search-поля пустыми None из лёгкого enrich
    if fallback is not None:
        title = title or fallback.title
        price = price if price is not None else fallback.price
        rating = rating if rating is not None else fallback.rating
        feedbacks = feedbacks if feedbacks is not None else fallback.feedbacks
        brand = brand or fallback.brand
        if photo_count is None:
            photo_count = fallback.photo_count
        sources.setdefault("commercial", "search_api")

    return CompetitorProduct(
        article=article,
        title=title,
        price=price,
        rating=rating,
        feedbacks=feedbacks,
        photos=photos,
        photo_count=photo_count,
        description=desc,
        brand=brand,
        category_id=getattr(fallback, "category_id", None) if fallback else None,
        category_name=getattr(fallback, "category_name", None) if fallback else None,
        match_score=match_score,
        sources=sources,
    )


@dataclass
class CompetitorContext:
    """Контекст конкурентов для Argus (to_prompt)."""

    main_product: MainProductSnapshot
    competitors: list[CompetitorProduct] = field(default_factory=list)
    analysis: CompetitorAnalysis | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "main_product": {
                "article": self.main_product.article,
                "title": self.main_product.title,
                "price": self.main_product.price,
                "rating": self.main_product.rating,
                "feedbacks": self.main_product.feedbacks,
            },
            "competitors": [c.as_dict() for c in self.competitors],
            "analysis": self.analysis.as_dict() if self.analysis else None,
        }

    def to_prompt(self) -> str:
        """Русский блок для Argus: список конкурентов + различия + вывод win/lose."""
        m = self.main_product
        lines: list[str] = [
            "=== КОНТЕКСТ КОНКУРЕНТОВ (CompetitorContext) ===",
            "",
            "## Основной товар",
            f"Артикул: {m.article}",
        ]
        if m.title:
            lines.append(f"Название: {m.title}")
        if m.brand:
            lines.append(f"Бренд: {m.brand}")
        if m.price is not None:
            lines.append(f"Цена: {m.price} руб.")
        else:
            lines.append("Цена: нет данных")
        if m.rating is not None:
            lines.append(f"Рейтинг: {m.rating}")
        else:
            lines.append("Рейтинг: нет данных")
        if m.feedbacks is not None:
            lines.append(f"Отзывов: {m.feedbacks}")
        else:
            lines.append("Отзывов: нет данных")
        if m.photo_count is not None:
            lines.append(f"Фотографий: {m.photo_count}")
        if m.has_description is True:
            lines.append("Описание: есть")
        elif m.has_description is False:
            lines.append("Описание: нет")

        lines.append("")
        lines.append("## Конкуренты")
        if not self.competitors:
            lines.append("(конкуренты не найдены)")
        else:
            for i, c in enumerate(self.competitors, 1):
                title = c.title or "без названия"
                lines.append(f"{i}. [{c.article}] {title}")
                bits: list[str] = []
                if c.price is not None:
                    bits.append(f"цена {c.price} ₽")
                if c.rating is not None:
                    bits.append(f"рейтинг {c.rating}")
                if c.feedbacks is not None:
                    bits.append(f"отзывов {c.feedbacks}")
                pc = c.photo_count if c.photo_count is not None else (
                    len(c.photos) if c.photos else None
                )
                if pc is not None:
                    bits.append(f"фото {pc}")
                if c.match_score is not None:
                    bits.append(f"score {c.match_score:.2f}")
                if bits:
                    lines.append("   " + " | ".join(bits))
                if c.description and str(c.description).strip():
                    preview = str(c.description).strip().replace("\n", " ")
                    if len(preview) > 180:
                        preview = preview[:180] + "…"
                    lines.append(f"   описание: {preview}")
                else:
                    lines.append("   описание: нет данных")
                if c.strengths:
                    lines.append("   сильные стороны конкурента: " + "; ".join(c.strengths[:4]))
                if c.weaknesses:
                    lines.append("   слабые стороны конкурента: " + "; ".join(c.weaknesses[:4]))

        analysis = self.analysis
        lines.append("")
        lines.append("## Различия")
        if analysis and analysis.differences:
            for d in analysis.differences:
                lines.append(f"- {d}")
        else:
            lines.append("(сравнение не выполнено)")

        if analysis:
            lines.append("")
            lines.append(f"## Позиция на рынке: {analysis.market_position}")
            if analysis.advantages:
                lines.append("Преимущества нашего товара:")
                for a in analysis.advantages:
                    lines.append(f"- {a}")
            if analysis.problems:
                lines.append("Проблемы относительно конкурентов:")
                for p in analysis.problems:
                    lines.append(f"- {p}")
            if analysis.recommendations:
                lines.append("Рекомендации:")
                for r in analysis.recommendations:
                    lines.append(f"- {r}")

        lines.append("")
        lines.append(
            "Сделай вывод: где мы выигрываем у конкурентов и где проигрываем. "
            "Опирайся только на факты выше, без выдуманных метрик."
        )
        return "\n".join(lines)


class CompetitorIntelligence:
    """
    Оркестратор MVP:
      ProductContext → search candidates → match top5 → light enrich → analyze.
    """

    def __init__(
        self,
        *,
        product_builder: Any = None,
        collector: CompetitorCollector | None = None,
        matcher: CompetitorMatcher | None = None,
        analyzer: CompetitorAnalyzer | None = None,
        proxy_pool: Any = None,
        enrich: bool = True,
        enrich_top_n: int = 5,
        max_search_queries: int = 2,
    ) -> None:
        self.product_builder = product_builder
        self.collector = collector or CompetitorCollector(
            proxy_pool=proxy_pool,
            max_queries=max_search_queries,
        )
        self.matcher = matcher or CompetitorMatcher(top_n=enrich_top_n)
        self.analyzer = analyzer or CompetitorAnalyzer()
        self.enrich = enrich
        self.enrich_top_n = max(1, int(enrich_top_n))

    async def _enrich_one(
        self,
        cand: CompetitorCandidate,
    ) -> CompetitorProduct:
        if self.product_builder is None or not self.enrich:
            return candidate_to_product(cand)

        await await_wb_rate_slot(label=f"enrich:{cand.article}")
        try:
            # Лёгкий ProductContext: без reviews/browser, чтобы не бить SOCKS.
            builder = self.product_builder
            # временно отключаем тяжёлые источники, если builder поддерживает флаги
            prev_reviews = getattr(builder, "reviews_service", None)
            prev_browser = getattr(builder, "allow_browser_fallback", True)
            prev_bp = getattr(builder, "browser_provider", None)
            try:
                if hasattr(builder, "reviews_service"):
                    builder.reviews_service = None
                if hasattr(builder, "allow_browser_fallback"):
                    builder.allow_browser_fallback = False
                if hasattr(builder, "browser_provider"):
                    builder.browser_provider = None
                ctx = await builder.build(int(cand.article))
            finally:
                if hasattr(builder, "reviews_service"):
                    builder.reviews_service = prev_reviews
                if hasattr(builder, "allow_browser_fallback"):
                    builder.allow_browser_fallback = prev_browser
                if hasattr(builder, "browser_provider"):
                    builder.browser_provider = prev_bp

            return product_context_to_competitor(
                ctx, match_score=cand.score, fallback=cand,
            )
        except Exception as exc:
            log.warning(
                "Competitor enrich failed article=%s: %s",
                cand.article,
                exc,
            )
            return candidate_to_product(cand)

    async def build_from_product_context(
        self,
        product_context: Any,
        *,
        category_id: int | None = None,
        category_name: str | None = None,
        candidates: list[CompetitorCandidate] | None = None,
    ) -> CompetitorContext:
        main = main_from_product_context(
            product_context,
            category_id=category_id,
            category_name=category_name,
        )
        # прокинем category на context для matcher
        try:
            setattr(product_context, "category_id", main.category_id)
            setattr(product_context, "category_name", main.category_name)
        except Exception:
            pass

        if candidates is None:
            candidates = await self.collector.collect(
                title=main.title,
                brand=main.brand,
                category_name=main.category_name,
                exclude_article=main.article,
            )

        # если category_id ещё нет — возьмём из самого частого кандидата с тем же brand
        if main.category_id is None and candidates:
            for c in candidates:
                if c.category_id is not None:
                    # предпочитаем совпадение brand
                    if main.brand and c.brand and c.brand.lower() == main.brand.lower():
                        main.category_id = c.category_id
                        main.category_name = main.category_name or c.category_name
                        break
            if main.category_id is None:
                main.category_id = candidates[0].category_id
                main.category_name = main.category_name or candidates[0].category_name
            try:
                setattr(product_context, "category_id", main.category_id)
                setattr(product_context, "category_name", main.category_name)
            except Exception:
                pass

        matched = self.matcher.find_similar_products(
            product_context,
            candidates,
            top_n=self.enrich_top_n,
        )

        # Если ProxyPool полностью на 30-мин block — не долбим enrich (search-полей достаточно).
        do_enrich = bool(self.enrich and self.product_builder is not None)
        if do_enrich:
            pool = getattr(self.collector, "proxy_pool", None)
            if pool is not None and getattr(pool, "proxies", None):
                try:
                    if not pool.has_available():
                        # короткая пауза rate-limit ещё возможна; 30-мин block → skip
                        import time as _time
                        hard_block = True
                        for p in getattr(pool, "_proxies", []) or []:
                            blocked_until = float(getattr(p, "blocked_until", 0.0) or 0.0)
                            if blocked_until <= _time.time():
                                hard_block = False
                                break
                        if hard_block:
                            log.info(
                                "CompetitorIntelligence: proxy hard-blocked → "
                                "skip enrich, use search commercial fields"
                            )
                            do_enrich = False
                except Exception:
                    pass

        competitors: list[CompetitorProduct] = []
        enrich_failures = 0
        for cand in matched:
            if do_enrich and enrich_failures < 2:
                item = await self._enrich_one(cand)
                # fallback-only результат без photos/description → считаем неудачей enrich
                if (
                    not item.photos
                    and not (item.description and str(item.description).strip())
                    and set(item.sources.keys()) <= {"commercial"}
                ):
                    enrich_failures += 1
                competitors.append(item)
            else:
                competitors.append(candidate_to_product(cand))
                # восстановим score_parts strengths later via analyzer
                if competitors[-1].match_score is None:
                    competitors[-1].match_score = cand.score

        analysis = self.analyzer.analyze(main, competitors)
        return CompetitorContext(
            main_product=main,
            competitors=competitors,
            analysis=analysis,
        )

    async def build(self, article: int) -> tuple[Any, CompetitorContext]:
        """
        Полный путь: ProductContextBuilder.build(article) + competitor layer.
        Возвращает (product_context, competitor_context).
        """
        if self.product_builder is None:
            raise RuntimeError("product_builder is required for CompetitorIntelligence.build")
        product_context = await self.product_builder.build(int(article))
        competitor_context = await self.build_from_product_context(product_context)
        return product_context, competitor_context
