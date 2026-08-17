"""
SessionService — рабочая память ARGUS о товаре пользователя.

Это «оперативная память» — быстрая, живёт в процессе бота,
нужна для плавного разговора прямо сейчас (get_product/get_analysis
остаются синхронными и мгновенными, как раньше).

Долговременная память — отдельно, в backend/memory/store.py.

seller-данные (цена/рейтинг/отзывы) хранятся отдельно от WBProduct
и привязаны к article — смена товара сбрасывает seller/discussion state.

Тексты отзывов (Review) — отдельно от feedbacks count продавца:
get/set_product_reviews(user_id, article_id) с изоляцией по user+article.
"""

import logging
import time

from backend.services.seller_data import SellerData

log = logging.getLogger("selleros.session")

#: Сколько последних реплик discussion держим в session.
_DISCUSSION_DEPTH = 20


def _canonical_photos_for_memory(product) -> int:
    """photo_count канонический — не len(DOM imgs)."""
    from backend.wb.provenance import canonical_photo_count

    return canonical_photo_count(product)


def _merge_wb_ids(product, *sources) -> None:
    """
    Сохранить/восстановить imt_id и root_id на объекте товара.
    None из нового ответа WB не затирает уже известные значения.
    """
    if product is None:
        return
    imt = getattr(product, "imt_id", None)
    root = getattr(product, "root_id", None)
    for src in sources:
        if src is None:
            continue
        if imt is None:
            imt = getattr(src, "imt_id", None)
        if root is None:
            root = getattr(src, "root_id", None)
    if imt is None and root is not None:
        imt = root
    if root is None and imt is not None:
        root = imt
    try:
        product.imt_id = imt
        product.root_id = root
    except Exception:
        pass


class SessionService:

    def __init__(self, memory_store=None):
        self._sessions: dict[int, dict] = {}
        self.memory_store = memory_store

    def _session(self, user_id: int) -> dict:
        return self._sessions.setdefault(user_id, {
            "product": None,
            "analysis": None,
            "seller_data": None,
            "seller_article": None,
            "product_reviews": None,          # list[Review] | None (None = ещё не грузили)
            "product_reviews_article": None,
            "product_context_prompt": None,   # optional ProductContext.to_prompt()
            "competitor_context_prompt": None,  # optional CompetitorContext.to_prompt()
            "competitor_comparison": None,
            "discussion_active": False,
            "discussion_article": None,
            "discussion_messages": [],
            "conversation_summary": "",
            "full_report_shown": False,
        })

    async def set_product(self, user_id: int, product, analysis: dict | None = None):
        session = self._session(user_id)
        previous = session.get("product")
        prev_article = getattr(previous, "article", None) if previous is not None else None
        new_article = getattr(product, "article", None)

        # Смена товара → сброс seller/discussion/full-report/reviews state.
        if prev_article is not None and new_article is not None and prev_article != new_article:
            session["seller_data"] = None
            session["seller_article"] = None
            session["product_reviews"] = None
            session["product_reviews_article"] = None
            session["product_context_prompt"] = None
            session["competitor_context_prompt"] = None
            session["competitor_comparison"] = None
            self.end_discussion(user_id)
            session["full_report_shown"] = False

        # Preserve imt/root: session previous (same article) + memory
        mem_rec = None
        if self.memory_store is not None and new_article is not None:
            try:
                mem_rec = await self.memory_store.get_product(
                    user_id, int(new_article), marketplace="wildberries",
                )
            except Exception as exc:
                log.debug("set_product: memory get_product skip: %s", exc)
                mem_rec = None

        same_article_prev = (
            previous if (
                previous is not None
                and prev_article is not None
                and new_article is not None
                and prev_article == new_article
            ) else None
        )
        _merge_wb_ids(product, same_article_prev, mem_rec)

        session["product"] = product
        if analysis is not None:
            session["analysis"] = analysis
        elif prev_article != new_article:
            session["analysis"] = None
            session["full_report_shown"] = False

        if self.memory_store is None:
            return

        await self.memory_store.touch_user(user_id)

        await self.memory_store.upsert_product(
            user_id=user_id,
            article=product.article,
            marketplace="wildberries",
            title=product.title or "Без названия",
            price=product.price,
            rating=product.rating,
            score=analysis.get("score") if analysis else None,
            photos=_canonical_photos_for_memory(product),
            imt_id=getattr(product, "imt_id", None),
            root_id=getattr(product, "root_id", None),
        )

        recommendations = analysis.get("recommendations") if analysis else None
        if recommendations:
            await self.memory_store.add_recommendations(
                user_id, product.article, recommendations,
            )

    def set_analysis(self, user_id: int, analysis: dict | None) -> None:
        """Обновить analysis без повторного upsert карточки."""
        self._session(user_id)["analysis"] = analysis

    def get_product(self, user_id: int):
        return self._session(user_id)["product"]

    def get_analysis(self, user_id: int) -> dict | None:
        return self._session(user_id)["analysis"]

    def has_product(self, user_id: int) -> bool:
        return self._session(user_id)["product"] is not None

    def clear_product(self, user_id: int, article: int | None = None) -> None:
        """
        Сбросить товар/анализ/seller/discussion/reviews из оперативной сессии.
        """
        session = self._session(user_id)
        current = session.get("product")

        if article is not None and current is not None and getattr(current, "article", None) != article:
            return

        session["product"] = None
        session["analysis"] = None
        session["seller_data"] = None
        session["seller_article"] = None
        session["product_reviews"] = None
        session["product_reviews_article"] = None
        session["product_context_prompt"] = None
        session["competitor_context_prompt"] = None
        session["competitor_comparison"] = None
        session["full_report_shown"] = False
        self.end_discussion(user_id)

    # --------------------------------------------------------- product reviews

    def get_product_reviews(self, user_id: int, article_id: int | None = None):
        """
        Тексты отзывов текущего товара пользователя.

        Returns:
            None — отзывы ещё не загружали для этого article (cache miss session);
            list  — загруженный набор (может быть пустым).

        Не смешивает товары/пользователей. Не трогает seller feedbacks count.
        """
        session = self._session(user_id)
        stored = session.get("product_reviews")
        bound = session.get("product_reviews_article")

        expected = article_id
        if expected is None:
            product = session.get("product")
            if product is not None:
                expected = getattr(product, "article", None)

        if expected is not None and bound is not None and int(bound) != int(expected):
            return None
        if stored is None:
            return None
        return list(stored)

    def set_product_reviews(self, user_id: int, article_id: int, reviews) -> None:
        """
        Сохранить отзывы article для пользователя.
        Не затирает seller_data (price/rating/feedbacks count).
        """
        session = self._session(user_id)
        session["product_reviews"] = list(reviews or [])
        session["product_reviews_article"] = int(article_id)

    def set_solution_research(self, user_id: int, result) -> None:
        """Кэш последнего SOLUTION_RESEARCH в сессии продавца."""
        self._session(user_id)["solution_research"] = result

    def get_solution_research(self, user_id: int):
        return self._session(user_id).get("solution_research")

    def set_pending_solution_pick(self, user_id: int, index_1based: int) -> None:
        self._session(user_id)["pending_solution_pick"] = int(index_1based)

    def get_pending_solution_pick(self, user_id: int) -> int | None:
        val = self._session(user_id).get("pending_solution_pick")
        return int(val) if val is not None else None

    def clear_pending_solution_pick(self, user_id: int) -> None:
        self._session(user_id).pop("pending_solution_pick", None)

    def set_product_decision_cache(
        self,
        user_id: int,
        article: int,
        topic: str,
        decision: dict,
    ) -> None:
        """Оперативный кэш решения (seller+article+topic)."""
        session = self._session(user_id)
        bucket = session.setdefault("product_decisions", {})
        key = f"{int(article)}:{(topic or '').strip().lower()}"
        bucket[key] = dict(decision or {})

    def get_product_decision_cache(
        self,
        user_id: int,
        article: int,
        topic: str,
    ) -> dict | None:
        session = self._session(user_id)
        bucket = session.get("product_decisions") or {}
        key = f"{int(article)}:{(topic or '').strip().lower()}"
        row = bucket.get(key)
        return dict(row) if isinstance(row, dict) else None

    def set_product_context_prompt(self, user_id: int, prompt: str | None) -> None:
        """
        Optional ProductContext.to_prompt() text for Argus context builder.
        Does not alter score/reasoner — only prompt input storage.
        """
        self._session(user_id)["product_context_prompt"] = (
            prompt.strip() if isinstance(prompt, str) and prompt.strip() else None
        )

    def get_product_context_prompt(self, user_id: int) -> str | None:
        text = self._session(user_id).get("product_context_prompt")
        return text if isinstance(text, str) and text.strip() else None

    def set_competitor_context_prompt(self, user_id: int, prompt: str | None) -> None:
        """
        Optional CompetitorContext.to_prompt() text for Argus context builder.
        Does not alter score/reasoner — only prompt input storage.
        """
        self._session(user_id)["competitor_context_prompt"] = (
            prompt.strip() if isinstance(prompt, str) and prompt.strip() else None
        )

    def get_competitor_context_prompt(self, user_id: int) -> str | None:
        text = self._session(user_id).get("competitor_context_prompt")
        return text if isinstance(text, str) and text.strip() else None

    def set_competitor_comparison(self, user_id: int, data) -> None:
        """Кэш CompetitorComparison (dict) для Advisor — без повторного Search."""
        if data is not None and hasattr(data, "as_dict"):
            data = data.as_dict()
        self._session(user_id)["competitor_comparison"] = data

    def get_competitor_comparison(self, user_id: int):
        return self._session(user_id).get("competitor_comparison")

    # --------------------------------------------------------- seller data

    async def set_seller_data(self, user_id: int, article: int, data: SellerData) -> None:
        """
        Сохранить данные продавца: в оперативный кэш (с привязкой к article)
        и в MemoryStore, если подключён.
        """
        session = self._session(user_id)
        session["seller_data"] = data
        session["seller_article"] = article

        if self.memory_store is None:
            return

        updated_at = data.updated_at.timestamp() if data.updated_at else time.time()

        await self.memory_store.save_seller_data(
            user_id=user_id,
            article=article,
            marketplace="wildberries",
            price=data.price,
            rating=data.rating,
            feedbacks=data.feedbacks,
            sales=data.sales,
            orders=data.orders,
            period=data.period,
            ctr=getattr(data, "ctr", None),
            cvr=getattr(data, "cvr", None),
            returns=getattr(data, "returns", None),
            ad_spend=getattr(data, "ad_spend", None),
            cost=getattr(data, "cost", None),
            commission=getattr(data, "commission", None),
            logistics=getattr(data, "logistics", None),
            storage=getattr(data, "storage", None),
            impressions=getattr(data, "impressions", None),
            views=getattr(data, "views", None),
            price_source=data.price_source,
            rating_source=data.rating_source,
            feedbacks_source=data.feedbacks_source,
            updated_at=updated_at,
        )

        # Dynamic Analytics: persist time-series snapshot (no invented metrics).
        try:
            from backend.ai.dynamic_analytics import persist_metric_snapshot
            product = session.get("product")
            fin_raw = session.get("finance_context")
            finance_ctx = None
            if isinstance(fin_raw, dict):
                try:
                    from backend.ai.finance_planner import FinancialContext
                    finance_ctx = FinancialContext.from_dict(fin_raw)
                except Exception:
                    finance_ctx = None
            await persist_metric_snapshot(
                self.memory_store,
                user_id,
                article,
                seller_data=data,
                product=product if getattr(product, "article", None) == article else None,
                finance_ctx=finance_ctx,
                source="session",
            )
        except Exception as exc:
            log.debug("set_seller_data: metric snapshot skip: %s", exc)

    def get_seller_data(
        self,
        user_id: int,
        article: int | None = None,
    ) -> SellerData | None:
        """
        SellerData из оперативного кэша.

        Если article передан (или в сессии есть product) — возвращаем данные
        только при совпадении артикула, чтобы не подтянуть чужой товар.
        """
        session = self._session(user_id)
        data = session.get("seller_data")
        if data is None:
            return None

        bound = session.get("seller_article")
        product = session.get("product")
        expected = article
        if expected is None and product is not None:
            expected = getattr(product, "article", None)

        if expected is not None and bound is not None and bound != expected:
            return None

        return data

    # --------------------------------------------------------- full report UI

    def mark_full_report_shown(self, user_id: int) -> None:
        self._session(user_id)["full_report_shown"] = True

    def is_full_report_shown(self, user_id: int) -> bool:
        return bool(self._session(user_id).get("full_report_shown"))

    # --------------------------------------------------------- discussion

    def start_discussion(self, user_id: int, article: int) -> bool:
        """
        Активировать discussion session для article.

        Returns True если это НОВАЯ сессия (историю надо сбросить),
        False если продолжаем ту же.
        """
        session = self._session(user_id)
        same = (
            session.get("discussion_active")
            and session.get("discussion_article") == article
        )
        if same:
            return False

        session["discussion_active"] = True
        session["discussion_article"] = article
        session["discussion_messages"] = []
        session["conversation_summary"] = ""
        return True

    def end_discussion(self, user_id: int) -> None:
        session = self._session(user_id)
        session["discussion_active"] = False
        session["discussion_article"] = None
        session["discussion_messages"] = []
        session["conversation_summary"] = ""

    def is_discussion_active(self, user_id: int, article: int | None = None) -> bool:
        session = self._session(user_id)
        if not session.get("discussion_active"):
            return False
        if article is not None and session.get("discussion_article") != article:
            return False
        return True

    def get_discussion_article(self, user_id: int) -> int | None:
        return self._session(user_id).get("discussion_article")

    def append_discussion_message(self, user_id: int, role: str, content: str) -> None:
        session = self._session(user_id)
        msgs = session.setdefault("discussion_messages", [])
        msgs.append({"role": role, "content": content})
        if len(msgs) > _DISCUSSION_DEPTH:
            del msgs[:-_DISCUSSION_DEPTH]

    def get_discussion_messages(self, user_id: int) -> list[dict]:
        return list(self._session(user_id).get("discussion_messages") or [])

    def set_conversation_summary(self, user_id: int, summary: str) -> None:
        self._session(user_id)["conversation_summary"] = summary or ""

    def get_conversation_summary(self, user_id: int) -> str:
        return self._session(user_id).get("conversation_summary") or ""

    # --------------------------------------------------------- finance / procurement

    def get_finance_context(self, user_id: int) -> dict | None:
        """Оперативная память финансовых допущений (закупка/логистика/маржа)."""
        raw = self._session(user_id).get("finance_context")
        return dict(raw) if isinstance(raw, dict) else None

    def set_finance_context(self, user_id: int, ctx: dict | None) -> None:
        session = self._session(user_id)
        if ctx is None:
            session.pop("finance_context", None)
        else:
            session["finance_context"] = dict(ctx)

    def clear_finance_context(self, user_id: int) -> None:
        self._session(user_id).pop("finance_context", None)

    # --------------------------------------------------------- funnel / unit economics

    def get_funnel_context(self, user_id: int) -> dict | None:
        """Оперативная память funnel-метрик (CTR/CVR/показы) в сессии."""
        raw = self._session(user_id).get("funnel_context")
        return dict(raw) if isinstance(raw, dict) else None

    def set_funnel_context(self, user_id: int, ctx: dict | None) -> None:
        session = self._session(user_id)
        if ctx is None:
            session.pop("funnel_context", None)
        else:
            session["funnel_context"] = dict(ctx)

    def clear_funnel_context(self, user_id: int) -> None:
        self._session(user_id).pop("funnel_context", None)

    # --------------------------------------------------------- dynamic analytics

    def get_dynamics_context(self, user_id: int) -> dict | None:
        """Оперативная память Dynamic Analytics (период/кейс) в сессии."""
        raw = self._session(user_id).get("dynamics_context")
        return dict(raw) if isinstance(raw, dict) else None

    def set_dynamics_context(self, user_id: int, ctx: dict | None) -> None:
        session = self._session(user_id)
        if ctx is None:
            session.pop("dynamics_context", None)
        else:
            session["dynamics_context"] = dict(ctx)

    def clear_dynamics_context(self, user_id: int) -> None:
        self._session(user_id).pop("dynamics_context", None)
