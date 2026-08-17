"""
AIAnalyzer — главный анализатор карточки.

Собирает всё вместе:
  1. ScoreCalculator          — считает оценку карточки (без AI, по правилам)
  2. RecommendationGenerator  — советы по правилам карточки
  3. CategoryIntelligence     — рыночные рекомендации из Intelligence Layer
  4. Actionable Advisor       — FACT → SIGNAL → CONFIDENCE → DIAGNOSIS → ACTION → PRIORITY → NOT_RECOMMENDED
  5. AIService                — живой комментарий от Seller AI (Gemini/Claude/OpenAI)
  6. ReportBuilder            — красивый итоговый отчёт для Telegram

Если CategoryIntelligence недоступен (category_intelligence=None) —
анализ работает как раньше: отчёт без рыночного блока.
Advisor строится всегда (хотя бы по карточке).
"""

import logging

from backend.ai.advisor import build_advisor_plan
from backend.ai.score import ScoreCalculator
from backend.ai.recommendations import RecommendationGenerator
from backend.ai.report import ReportBuilder
from backend.ai.prompts import build_analysis_system, build_product_analysis_prompt

log = logging.getLogger("selleros.analyzer")


class AIAnalyzer:

    def __init__(
        self,
        ai_service=None,
        category_intelligence=None,
        outcome_tracker=None,
        review_intel=None,
    ):
        # AIService — единственный экземпляр на проект (передаётся из bot.py).
        # category_intelligence — опционально; если None, рыночный блок не строится.
        # review_intel — опционально; Advisor использует RI если assessment передан
        #   в analyze() или через reviews payload.
        # outcome_tracker — опционально; на v1 не вызывается автоматически.
        self.ai = ai_service
        self._ci = category_intelligence
        self._outcome_tracker = outcome_tracker
        self._ri = review_intel

        self.score_calculator = ScoreCalculator()
        self.recommendation_generator = RecommendationGenerator(
            outcome_tracker=outcome_tracker,
        )
        self.report_builder = ReportBuilder()

    async def analyze(
        self,
        product,
        with_ai: bool = True,
        *,
        seller_data=None,
        review_assessment=None,
        reviews: list | None = None,
    ) -> dict:
        """
        product — объект WBProduct (см. backend/wb/cdn_provider.py).

        Возвращает dict:
            score, reasons, recommendations, ai_comment,
            market_recommendations, advisor_plan, advisor_text,
            report, caption
        """

        score_data = self.score_calculator.calculate(product)
        recommendations = self.recommendation_generator.generate(product)

        # ── Market recommendations (Intelligence Layer) ──────────────── #
        market_recs = []
        category_context = None
        if self._ci is not None:
            category = getattr(product, "subject_name", None)
            if category:
                try:
                    category_context = await self._ci.analyze(
                        category=category,
                        region="RU",
                        limit=20,
                    )
                    market_recs = self.recommendation_generator.generate_market_recommendations(
                        category_context
                    )
                except Exception as exc:
                    log.warning(
                        "AIAnalyzer: market recs недоступны для %r: %s",
                        category, exc,
                    )

        # ── Review assessment (optional, production path) ─────────────── #
        assessment = review_assessment
        if assessment is None and reviews and self._ri is not None:
            try:
                assessment = await self._ri.analyze(
                    reviews,
                    category=getattr(product, "subject_name", None),
                    article=str(getattr(product, "article", "") or "") or None,
                    persist=False,
                )
            except Exception as exc:
                log.warning("AIAnalyzer: RI недоступен: %s", exc)
                assessment = None

        review_recs = []
        if assessment is not None:
            try:
                review_recs = self.recommendation_generator.generate_review_recommendations(
                    assessment
                )
            except Exception:
                review_recs = []

        # ── Actionable Advisor (deterministic) ────────────────────────── #
        advisor_plan = build_advisor_plan(
            product=product,
            score_data=score_data,
            seller_data=seller_data,
            review_assessment=assessment,
            category_context=category_context,
            card_recommendations=recommendations,
            market_recommendations=market_recs,
            review_recommendations=review_recs,
        )
        advisor_text = advisor_plan.format_plain() if advisor_plan.has_content() else ""

        # ── AI comment ───────────────────────────────────────────────── #
        ai_comment = None
        if with_ai and self.ai is not None:
            prompt = build_product_analysis_prompt(
                product,
                advisor_text=advisor_text or None,
            )
            ai_comment = await self.ai.generate(
                prompt,
                system=build_analysis_system(),
            )
            if ai_comment is None:
                log.warning("AI недоступен — отчёт будет без AI-комментария")

        report = self.report_builder.build_card(
            product=product,
            score_data=score_data,
            recommendations=recommendations,
            ai_comment=ai_comment,
            market_recs=market_recs or None,
            advisor_plan=advisor_plan,
        )

        caption = self.report_builder.build_caption(
            product=product,
            score=score_data["score"],
        )

        return {
            "score": score_data["score"],
            "reasons": score_data["reasons"],
            "score_breakdown": score_data.get("breakdown"),
            "score_scope": score_data.get("scope", "card_only"),
            "recommendations": recommendations,
            "market_recommendations": market_recs,
            "advisor_plan": advisor_plan,
            "advisor_text": advisor_text,
            "diagnosis_snapshot": (
                advisor_plan.diagnosis_snapshot()
                if hasattr(advisor_plan, "diagnosis_snapshot")
                else None
            ),
            "review_assessment": assessment,
            "ai_comment": ai_comment,
            "report": report,
            "caption": caption,
        }
