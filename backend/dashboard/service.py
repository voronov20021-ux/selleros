"""Dashboard service — ProductContext / CI / Argus with mock fallback."""

from __future__ import annotations

import logging
from typing import Any, Optional

from backend.dashboard.schemas import (
    CompetitorsBlock,
    DashboardAlert,
    DashboardMetrics,
    DashboardProduct,
    SellerDashboardResponse,
    SellerInfo,
)

log = logging.getLogger("selleros.dashboard")

_DEMO: list[dict[str, Any]] = [
    {
        "article": 279904819,
        "title": "Футболка базовая хлопок премиум",
        "image": "https://basket-12.wbbasket.ru/vol2799/part279904/279904819/images/c246x328/1.webp",
        "price": 994,
        "rating": 4.4,
        "reviews_count": 1287,
        "position": 18,
        "argus_score": 62,
        "argus_status": "YELLOW",
        "problems": ["Слабые SEO-ключи", "Мало фото на модели", "Короткое описание"],
        "recommendations": ["Добавить ключи в title", "Дописать УТП", "Добавить lifestyle-фото"],
        "tags": ["attention", "drop"],
        "competitor_price": 890,
    },
    {
        "article": 312445901,
        "title": "Кроссовки беговые лёгкие",
        "image": "https://basket-10.wbbasket.ru/vol3124/part312445/312445901/images/c246x328/1.webp",
        "price": 3490,
        "rating": 4.7,
        "reviews_count": 3421,
        "position": 7,
        "argus_score": 81,
        "argus_status": "GREEN",
        "problems": [],
        "recommendations": ["Усилить рекламу на пике спроса"],
        "tags": ["top", "growth"],
        "competitor_price": 3990,
    },
    {
        "article": 155667788,
        "title": "Плойка для волос керамика",
        "image": "https://basket-05.wbbasket.ru/vol1556/part155667/155667788/images/c246x328/1.webp",
        "price": 1890,
        "rating": 3.9,
        "reviews_count": 412,
        "position": 64,
        "argus_score": 41,
        "argus_status": "RED",
        "problems": ["Низкий рейтинг", "Нет инфографики", "SEO дубли"],
        "recommendations": ["Закрыть жалобы по нагреву", "Добавить инфографику"],
        "tags": ["attention", "drop"],
        "competitor_price": 2190,
    },
    {
        "article": 401122334,
        "title": "Органайзер для косметики",
        "image": "https://basket-15.wbbasket.ru/vol4011/part401122/401122334/images/c246x328/1.webp",
        "price": 690,
        "rating": 4.6,
        "reviews_count": 890,
        "position": 12,
        "argus_score": 74,
        "argus_status": "GREEN",
        "problems": [],
        "recommendations": ["Добавить ключ «акрил» в title"],
        "tags": ["top", "growth"],
        "competitor_price": 750,
    },
    {
        "article": 288776655,
        "title": "Сумка шоппер экокожа",
        "image": "https://basket-11.wbbasket.ru/vol2887/part288776/288776655/images/c246x328/1.webp",
        "price": 1590,
        "rating": 4.2,
        "reviews_count": 556,
        "position": 29,
        "argus_score": 55,
        "argus_status": "YELLOW",
        "problems": ["Падение позиции", "Слабые фото деталей"],
        "recommendations": ["Обновить главное фото", "Дописать размеры"],
        "tags": ["attention", "drop"],
        "competitor_price": 1490,
    },
]


def _status(score: int) -> str:
    if score >= 75:
        return "GREEN"
    if score >= 50:
        return "YELLOW"
    return "RED"


class DashboardService:
    def __init__(
        self,
        memory_store=None,
        product_builder=None,
        competitor_intelligence=None,
        analyzer=None,
        force_demo: bool = False,
    ):
        self._memory = memory_store
        self._builder = product_builder
        self._ci = competitor_intelligence
        self._analyzer = analyzer
        self._force_demo = force_demo

    def _catalog(self) -> tuple[list[dict[str, Any]], bool]:
        if self._force_demo:
            return [dict(p) for p in _DEMO], True
        # Live MemoryStore probe (sync APIs only; async skipped → demo)
        if self._memory is not None:
            try:
                for name in ("list_all_products", "get_all_products"):
                    fn = getattr(self._memory, name, None)
                    if callable(fn):
                        rows = fn()
                        if rows:
                            out = []
                            for row in rows:
                                d = row if isinstance(row, dict) else getattr(row, "__dict__", {})
                                art = d.get("article") or getattr(row, "article", None)
                                if not art:
                                    continue
                                score = int(d.get("score") or d.get("argus_score") or 60)
                                out.append(
                                    {
                                        "article": int(art),
                                        "title": d.get("title") or str(art),
                                        "image": d.get("image"),
                                        "price": d.get("price"),
                                        "rating": d.get("rating"),
                                        "reviews_count": d.get("reviews_count") or d.get("feedbacks"),
                                        "position": d.get("position"),
                                        "argus_score": score,
                                        "argus_status": _status(score),
                                        "problems": list(d.get("problems") or []),
                                        "recommendations": list(d.get("recommendations") or []),
                                        "tags": ["attention"] if score < 75 else ["top"],
                                        "competitor_price": None,
                                    }
                                )
                            if out:
                                return out, False
            except Exception as exc:
                log.warning("MemoryStore catalog failed: %s", exc)
        return [dict(p) for p in _DEMO], True

    async def _try_product_context(self, article: int, base: dict[str, Any]) -> dict[str, Any]:
        if self._builder is None:
            return base
        try:
            from backend.product_context import ProductContext  # noqa: F401

            ctx = await self._builder.build(int(article))
            if ctx is None:
                return base
            m = dict(base)
            if ctx.product.title:
                m["title"] = ctx.product.title
            if ctx.pricing.price is not None:
                m["price"] = ctx.pricing.price
            if ctx.pricing.rating is not None:
                m["rating"] = ctx.pricing.rating
            if ctx.pricing.feedback_count is not None:
                m["reviews_count"] = ctx.pricing.feedback_count
            if ctx.media.photos:
                m["image"] = ctx.media.photos[0]
            m["_product_context"] = True
            return m
        except Exception as exc:
            log.warning("ProductContext failed %s: %s", article, exc)
            return base

    async def _try_argus(self, article: int, base: dict[str, Any]) -> dict[str, Any]:
        if self._analyzer is None:
            return base
        try:
            class _Card:
                """Duck-typed product for ScoreCalculator / RecommendationGenerator."""

                def __init__(self, data: dict[str, Any]):
                    photos = [data["image"]] if data.get("image") else []
                    self.id = int(article)
                    self.article = int(article)
                    self.name = data.get("title") or str(article)
                    self.title = self.name
                    self.brand = ""
                    self.seller = ""
                    self.price = int(data.get("price") or 0)
                    self.old_price = data.get("old_price")
                    self.discount = None
                    self.rating = float(data.get("rating") or 0)
                    self.reviews = int(data.get("reviews_count") or 0)
                    self.feedbacks = self.reviews
                    self.photos = photos
                    self.description = str(data.get("description") or "")
                    self.characteristics = {}
                    self.sizes = []
                    self.subject_name = None
                    self.subject_id = None

                def __getattr__(self, _name: str):
                    return None

            analysis = await self._analyzer.analyze(_Card(base), with_ai=False)
            score = int(analysis.get("score") or base.get("argus_score") or 60)
            m = dict(base)
            m["argus_score"] = score
            m["argus_status"] = _status(score)
            reasons = list(analysis.get("reasons") or [])
            if reasons:
                m["problems"] = reasons[:6]
            recs = []
            for r in analysis.get("recommendations") or []:
                recs.append(r if isinstance(r, str) else getattr(r, "text", None) or str(r))
            if recs:
                m["recommendations"] = recs[:6]
            m["_argus"] = True
            return m
        except Exception as exc:
            log.warning("Argus failed %s: %s", article, exc)
            return base

    async def get_seller_dashboard(self, seller_id: str) -> SellerDashboardResponse:
        products_raw, demo = self._catalog()

        # Optional enrich first product via ProductContext / Argus (mock-safe)
        if products_raw:
            p0 = await self._try_product_context(int(products_raw[0]["article"]), products_raw[0])
            p0 = await self._try_argus(int(p0["article"]), p0)
            products_raw[0] = p0
            if p0.get("_product_context") or p0.get("_argus"):
                demo = False

        products = [
            DashboardProduct(
                article=int(p["article"]),
                title=p["title"],
                price=p.get("price"),
                rating=p.get("rating"),
                reviews_count=p.get("reviews_count"),
                argus_status=p.get("argus_status") or "YELLOW",  # type: ignore[arg-type]
                recommendations=list(p.get("recommendations") or []),
                image=p.get("image"),
                position=p.get("position"),
                problems=list(p.get("problems") or []),
                argus_score=p.get("argus_score"),
            )
            for p in products_raw
        ]

        scores = [int(p.argus_score or 60) for p in products]
        argus_index = int(round(sum(scores) / len(scores))) if scores else 60
        problems_count = sum(1 for p in products if p.argus_status in ("RED", "YELLOW") or p.problems)
        opportunities_count = sum(1 for p in products if p.argus_status == "GREEN")

        # Competitors block (mock or light from demo prices)
        top_comp = []
        diffs = []
        for p in products_raw:
            cp = p.get("competitor_price")
            if cp is not None and p.get("price") is not None:
                diffs.append(float(p["price"]) - float(cp))
                top_comp.append(
                    {
                        "article": p["article"],
                        "title": p["title"],
                        "our_price": p["price"],
                        "competitor_price": cp,
                        "rating": p.get("rating"),
                    }
                )
        avg_diff = round(sum(diffs) / len(diffs), 1) if diffs else 0.0
        if argus_index >= 75:
            market_position = "strong"
        elif argus_index >= 50:
            market_position = "mid"
        else:
            market_position = "weak"

        # Try Competitor Intelligence import (presence hook; no WBEngine mutate)
        try:
            from backend.competitor_intelligence import CompetitorIntelligence  # noqa: F401
        except Exception:
            pass

        alerts: list[DashboardAlert] = []
        for p in products:
            if p.argus_status == "RED":
                alerts.append(
                    DashboardAlert(
                        type="critical",
                        message=f"{p.title}: критичный Argus ({p.argus_status})",
                        priority="high",
                    )
                )
            elif p.problems:
                alerts.append(
                    DashboardAlert(
                        type="attention",
                        message=f"{p.title}: {p.problems[0]}",
                        priority="medium",
                    )
                )

        name = f"Seller {seller_id}" if seller_id else "Seller"
        if str(seller_id).lower() in ("demo", "1", "seller"):
            name = "Seller OS Demo"

        return SellerDashboardResponse(
            seller=SellerInfo(
                id=str(seller_id),
                name=name,
                subscription="demo" if demo else "pro",
            ),
            metrics=DashboardMetrics(
                argus_index=argus_index,
                products_count=len(products),
                problems_count=problems_count,
                opportunities_count=opportunities_count,
            ),
            products=products,
            competitors=CompetitorsBlock(
                top_products=top_comp[:5],
                market_position=market_position,
                price_difference=avg_diff,
            ),
            alerts=alerts[:10],
            demo=demo,
        )

    # --- extras kept for Mini App filters ---

    def filter_products(
        self, seller_id: str, filter_name: str = "all"
    ) -> list[DashboardProduct]:
        data = None
        # sync path using catalog only
        products_raw, _ = self._catalog()
        key = (filter_name or "all").lower()
        out = []
        for p in products_raw:
            tags = set(p.get("tags") or [])
            status = p.get("argus_status") or "YELLOW"
            ok = False
            if key == "all":
                ok = True
            elif key in ("attention", "требует внимания") and (
                "attention" in tags or status in ("RED", "YELLOW")
            ):
                ok = True
            elif key in ("top", "топ", "топ продаж", "growth", "рост") and (
                "top" in tags or "growth" in tags or status == "GREEN"
            ):
                ok = True
            elif key in ("drop", "просели") and ("drop" in tags or status == "RED"):
                ok = True
            if ok:
                out.append(
                    DashboardProduct(
                        article=int(p["article"]),
                        title=p["title"],
                        price=p.get("price"),
                        rating=p.get("rating"),
                        reviews_count=p.get("reviews_count"),
                        argus_status=status,  # type: ignore[arg-type]
                        recommendations=list(p.get("recommendations") or []),
                        image=p.get("image"),
                        position=p.get("position"),
                        problems=list(p.get("problems") or []),
                        argus_score=p.get("argus_score"),
                    )
                )
        return out

    def get_catalog_item(self, article: int) -> tuple[Optional[dict[str, Any]], bool]:
        """Lookup one catalog row. Does not recompute Argus."""
        products, demo = self._catalog()
        for p in products:
            try:
                if int(p.get("article") or 0) == int(article):
                    return dict(p), demo
            except (TypeError, ValueError):
                continue
        return None, demo
