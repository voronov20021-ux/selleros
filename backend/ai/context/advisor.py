"""
ai/context/advisor.py — Actionable Advisor контекст для Argus.

AdvisorContextSource:
    product + RI assessment + Category Intelligence + card heuristics
        → build_advisor_plan (deterministic)
        → ContextBlock (priority=35)

Не отдельный «мозг»: тот же Advisor, что в отчётах.
Не выдумывает факты. IDEA ≠ buyer demand.
"""

from __future__ import annotations

import logging

from backend.ai.advisor import build_advisor_plan
from backend.ai.context.base import ContextBlock, ContextRequest, ContextSource
from backend.ai.intents import Intent
from backend.ai.recommendations import RecommendationGenerator
from backend.memory.context import make_user_hash

log = logging.getLogger("selleros.ai.context.advisor")

_RELEVANT_INTENTS = frozenset({
    Intent.PRODUCT_DISCUSSION,
    Intent.REVIEWS,
    Intent.MARKETING,
    Intent.PRICING,
    Intent.PHOTO,
    Intent.SELLER_ANALYTICS,
    Intent.GENERAL_QUESTION,
    Intent.COMPETITOR,
})

_MAX_BLOCK_CHARS = 1600


def _reviews_to_payload(raw_reviews) -> list[dict]:
    out: list[dict] = []
    for item in raw_reviews or []:
        if item is None:
            continue
        if hasattr(item, "to_ri_dict"):
            out.append(item.to_ri_dict())
            continue
        if isinstance(item, dict):
            text = item.get("text") or item.get("content") or item.get("review_text") or ""
            if not str(text).strip():
                continue
            out.append(item)
            continue
        if isinstance(item, str) and item.strip():
            out.append({"text": item})
    return out


class AdvisorContextSource(ContextSource):
    """
    Собирает AdvisorPlan в production-контексте SellerBrain.

    review_intel — ReviewIntelligence | None
    category_intelligence — CategoryIntelligence | None
    session — SessionService
    reviews_service — WBReviewsService | None
    """

    name = "advisor"
    intents = _RELEVANT_INTENTS
    priority = 35

    def __init__(
        self,
        session=None,
        review_intel=None,
        category_intelligence=None,
        reviews_service=None,
        recommendation_generator=None,
    ):
        self.session = session
        self._ri = review_intel
        self._ci = category_intelligence
        self._reviews_svc = reviews_service
        self._rec_gen = recommendation_generator or RecommendationGenerator()

    async def fetch(self, request: ContextRequest) -> ContextBlock | None:
        if self.session is None:
            return None
        try:
            product = self.session.get_product(request.user_id)
            if product is None:
                return None

            article_raw = getattr(product, "article", None)
            article_id = int(article_raw) if article_raw is not None else None
            article = str(article_raw) if article_raw is not None else None
            category = getattr(product, "subject_name", None) or None
            user_hash = make_user_hash(request.user_id)

            # Score / card recommendations (deterministic)
            score_data = None
            card_recs: list = []
            try:
                from backend.ai.score import ScoreCalculator
                score_data = ScoreCalculator().calculate(product)
                card_recs = self._rec_gen.generate(product)
            except Exception as exc:
                log.debug("AdvisorContextSource score/recs skipped: %s", exc)

            # Seller data if present
            seller_data = None
            if article_id is not None and hasattr(self.session, "get_seller_data"):
                try:
                    seller_data = self.session.get_seller_data(request.user_id, article_id)
                except TypeError:
                    seller_data = self.session.get_seller_data(request.user_id)
                except Exception:
                    seller_data = None

            # Reviews → RI
            assessment = None
            raw_reviews = None
            if hasattr(self.session, "get_product_reviews") and article_id is not None:
                raw_reviews = self.session.get_product_reviews(request.user_id, article_id)

            if raw_reviews is None and self._reviews_svc is not None and article_id is not None:
                try:
                    raw_reviews = await self._reviews_svc.load_into_session(
                        self.session, request.user_id, product,
                    )
                except Exception as exc:
                    log.warning("AdvisorContextSource reviews fetch failed: %s", exc)
                    raw_reviews = []

            extra = getattr(request, "extra", None) or {}
            if raw_reviews is None and extra.get("reviews") is not None:
                raw_reviews = extra.get("reviews")
            if extra.get("review_assessment") is not None:
                assessment = extra["review_assessment"]

            payload = _reviews_to_payload(raw_reviews) if raw_reviews else []
            if assessment is None and payload and self._ri is not None:
                assessment = await self._ri.analyze(
                    payload,
                    category=category,
                    article=article,
                    user_hash=user_hash,
                    persist=True,
                )

            review_recs = []
            if assessment is not None:
                try:
                    review_recs = self._rec_gen.generate_review_recommendations(assessment)
                except Exception:
                    review_recs = []

            # Category Intelligence
            category_context = extra.get("category_context")
            market_recs: list = []
            if category_context is None and self._ci is not None and category:
                try:
                    category_context = await self._ci.analyze(
                        category=category, region="RU", limit=20,
                    )
                except Exception as exc:
                    log.warning("AdvisorContextSource CI failed: %s", exc)
                    category_context = None
            if category_context is not None:
                try:
                    market_recs = self._rec_gen.generate_market_recommendations(category_context)
                except Exception:
                    market_recs = []

            # Decisions (optional, product-scoped)
            decisions = None
            if extra.get("decisions") is not None:
                decisions = extra["decisions"]

            # Saved diagnosis from last analysis (discussion memory)
            saved_snapshot = extra.get("diagnosis_snapshot")
            if saved_snapshot is None and hasattr(self.session, "get_analysis"):
                try:
                    analysis = self.session.get_analysis(request.user_id)
                    if isinstance(analysis, dict):
                        saved_snapshot = analysis.get("diagnosis_snapshot")
                        if saved_snapshot is None:
                            plan_obj = analysis.get("advisor_plan")
                            if plan_obj is not None and hasattr(plan_obj, "diagnosis_snapshot"):
                                saved_snapshot = plan_obj.diagnosis_snapshot()
                except Exception:
                    saved_snapshot = None

            competitor_comparison = extra.get("competitor_comparison")
            if competitor_comparison is None and hasattr(self.session, "get_competitor_comparison"):
                try:
                    competitor_comparison = self.session.get_competitor_comparison(request.user_id)
                except Exception:
                    competitor_comparison = None
            if isinstance(competitor_comparison, dict):
                try:
                    from backend.ai.advisor import _comparison_from_meta
                    competitor_comparison = _comparison_from_meta(competitor_comparison)
                except Exception:
                    competitor_comparison = None

            plan = build_advisor_plan(
                product=product,
                score_data=score_data,
                seller_data=seller_data,
                review_assessment=assessment,
                category_context=category_context,
                card_recommendations=card_recs,
                market_recommendations=market_recs,
                review_recommendations=review_recs,
                decisions=decisions,
                competitor_comparison=competitor_comparison,
            )
            if not plan.has_content():
                return None

            # Focus from quick-action / text hint
            focus = None
            text_l = (request.text or "").lower().replace("ё", "е")
            if any(k in text_l for k in ("как исправ", "что чинить", "как улучш")):
                focus = "fixes"
            elif any(k in text_l for k in ("что добав", "чего не хват")):
                focus = "add"
            elif any(k in text_l for k in ("как увелич", "как вырасти", "как выраст", "продаж")):
                focus = "grow"
            from backend.ai.advisor import format_advisor_focus
            body = format_advisor_focus(plan, focus) if focus else plan.to_context_block(
                max_chars=_MAX_BLOCK_CHARS,
            )

            # Inject saved diagnosis + decisions for «что решили» discussion
            diag_block = ""
            snap = saved_snapshot or (
                plan.diagnosis_snapshot() if hasattr(plan, "diagnosis_snapshot") else None
            )
            if snap:
                decisions_lines = []
                for d in list(snap.get("decisions") or [])[:3]:
                    if isinstance(d, dict):
                        decisions_lines.append(
                            f"- topic={d.get('topic')}; status={d.get('status')}; "
                            f"choice={d.get('seller_choice') or d.get('selected_solution_id')}"
                        )
                actions = list(snap.get("actions") or [])[:3]
                not_rec = list(snap.get("not_recommended") or [])[:3]
                diag_block = (
                    "\n\nDIAGNOSIS SNAPSHOT (сохранённый диагноз — не противоречь):\n"
                    f"locus={snap.get('locus')}; bottleneck={snap.get('bottleneck')}\n"
                    f"diagnosis={snap.get('diagnosis')}\n"
                    f"main_problem={snap.get('main_problem')}\n"
                    f"main_verdict={snap.get('main_verdict')}\n"
                    f"do_first={snap.get('do_first')}\n"
                    f"leave_alone={snap.get('leave_alone')}\n"
                    f"priority={snap.get('priority_tier')}; "
                    f"confidence={snap.get('confidence_label')}\n"
                    f"data_needed={snap.get('data_needed')}\n"
                    + (
                        "actions:\n" + "\n".join(f"- {a}" for a in actions) + "\n"
                        if actions else ""
                    )
                    + (
                        "not_recommended:\n" + "\n".join(f"- {n}" for n in not_rec) + "\n"
                        if not_rec else ""
                    )
                    + (
                        "decisions:\n" + "\n".join(decisions_lines) + "\n"
                        if decisions_lines else ""
                    )
                    + "Вопросы про цену / «что решили» / «что мы решили?» / «с чего начать» — "
                    "опирайся на этот снимок (диагноз + решения), не предлагай шаблон вразрез."
                )
            if focus:
                # wrap focus with header for LLM
                body = (
                    "ADVISOR PLAN (ANALYTICAL)\n"
                    "Цепочка: FACT → SIGNAL → CONFIDENCE → DIAGNOSIS → ACTION → PRIORITY → NOT_RECOMMENDED.\n"
                    "Слои: FACT / INFERENCE / RECOMMENDATION / IDEA.\n"
                    "ЗНАЕМ / ПРЕДПОЛАГАЕМ / НУЖНО ПРОВЕРИТЬ.\n"
                    "IDEA ≠ спрос покупателей.\n"
                    "Не противоречь диагнозу (locus).\n\n"
                    + body
                    + diag_block
                )
            elif diag_block:
                body = body + diag_block
            if len(body) > _MAX_BLOCK_CHARS:
                body = body[: _MAX_BLOCK_CHARS - 1].rstrip() + "…"

            return ContextBlock(
                title="СОВЕТНИК ARGUS (ANALYTICAL)",
                body=body,
                priority=self.priority,
            )
        except Exception as exc:
            log.warning("AdvisorContextSource failed: %s", exc)
            return None
