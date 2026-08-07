"""
AIAnalyzer — главный анализатор карточки.

Собирает всё вместе:
  1. ScoreCalculator      — считает оценку карточки (без AI, по правилам)
  2. RecommendationGenerator — советы по правилам
  3. AIService            — живой комментарий от Seller AI (Gemini/Claude/OpenAI)
  4. ReportBuilder        — красивый итоговый отчёт для Telegram

Этот файл раньше был сломан (IndentationError) —
из-за него не запускался весь бот.
"""

import logging

from backend.ai.score import ScoreCalculator
from backend.ai.recommendations import RecommendationGenerator
from backend.ai.report import ReportBuilder
from backend.ai.prompts import build_analysis_system, build_product_analysis_prompt

log = logging.getLogger("selleros.analyzer")


class AIAnalyzer:

    def __init__(self, ai_service=None):
        # AIService передаём снаружи (из bot.py),
        # чтобы на весь проект был ОДИН экземпляр.
        # Если не передали — работаем без AI-комментария.
        self.ai = ai_service

        self.score_calculator = ScoreCalculator()
        self.recommendation_generator = RecommendationGenerator()
        self.report_builder = ReportBuilder()

    async def analyze(self, product, with_ai: bool = True) -> dict:
        """
        product — объект WBProduct (см. backend/wb/cdn_provider.py).

        Возвращает dict:
            score, reasons, recommendations, ai_comment, report
        """

        score_data = self.score_calculator.calculate(product)

        recommendations = self.recommendation_generator.generate(product)

        ai_comment = None

        if with_ai and self.ai is not None:
            prompt = build_product_analysis_prompt(product)
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
        )

        caption = self.report_builder.build_caption(
            product=product,
            score=score_data["score"],
        )

        return {
            "score": score_data["score"],
            "reasons": score_data["reasons"],
            "recommendations": recommendations,
            "ai_comment": ai_comment,
            "report": report,
            "caption": caption,
        }
