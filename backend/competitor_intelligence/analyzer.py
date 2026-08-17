"""
CompetitorAnalyzer — сравнение main vs competitors → CompetitorAnalysis.
"""

from __future__ import annotations

from statistics import median
from typing import Any, Sequence

from backend.competitor_intelligence.models import (
    CompetitorAnalysis,
    CompetitorProduct,
    MainProductSnapshot,
)


def _photo_n(item: Any) -> int | None:
    pc = getattr(item, "photo_count", None)
    if pc is not None:
        try:
            return int(pc)
        except (TypeError, ValueError):
            pass
    photos = getattr(item, "photos", None) or []
    if photos:
        return len(photos)
    return None


def _has_description(item: Any) -> bool | None:
    desc = getattr(item, "description", None)
    if isinstance(desc, str):
        return bool(desc.strip())
    flag = getattr(item, "has_description", None)
    if isinstance(flag, bool):
        return flag
    return None


def _fill_relative_sw(
    main: MainProductSnapshot,
    competitor: CompetitorProduct,
) -> None:
    """Сильные/слабые стороны конкурента относительно нашего товара."""
    strengths: list[str] = []
    weaknesses: list[str] = []

    if main.price is not None and competitor.price is not None:
        if competitor.price < main.price:
            strengths.append(
                f"дешевле нашего ({competitor.price} vs {main.price} ₽)"
            )
        elif competitor.price > main.price:
            weaknesses.append(
                f"дороже нашего ({competitor.price} vs {main.price} ₽)"
            )

    if main.rating is not None and competitor.rating is not None:
        if competitor.rating > main.rating + 0.05:
            strengths.append(
                f"рейтинг выше ({competitor.rating} vs {main.rating})"
            )
        elif competitor.rating + 0.05 < main.rating:
            weaknesses.append(
                f"рейтинг ниже ({competitor.rating} vs {main.rating})"
            )

    if main.feedbacks is not None and competitor.feedbacks is not None:
        if competitor.feedbacks > main.feedbacks:
            strengths.append(
                f"больше отзывов ({competitor.feedbacks} vs {main.feedbacks})"
            )
        elif competitor.feedbacks < main.feedbacks:
            weaknesses.append(
                f"меньше отзывов ({competitor.feedbacks} vs {main.feedbacks})"
            )

    main_photos = main.photo_count
    comp_photos = _photo_n(competitor)
    if main_photos is not None and comp_photos is not None:
        if comp_photos > main_photos:
            strengths.append(f"больше фото ({comp_photos} vs {main_photos})")
        elif comp_photos < main_photos:
            weaknesses.append(f"меньше фото ({comp_photos} vs {main_photos})")

    main_desc = main.has_description
    comp_desc = _has_description(competitor)
    if main_desc is False and comp_desc is True:
        strengths.append("есть описание (у нас нет)")
    elif main_desc is True and comp_desc is False:
        weaknesses.append("нет описания (у нас есть)")

    competitor.strengths = strengths
    competitor.weaknesses = weaknesses


class CompetitorAnalyzer:
    """Сравнивает цену / рейтинг / отзывы / фото / описание."""

    def analyze(
        self,
        main: MainProductSnapshot,
        competitors: Sequence[CompetitorProduct],
    ) -> CompetitorAnalysis:
        comps = list(competitors)
        for c in comps:
            _fill_relative_sw(main, c)

        advantages: list[str] = []
        problems: list[str] = []
        recommendations: list[str] = []
        differences: list[str] = []

        prices = [c.price for c in comps if c.price is not None]
        ratings = [c.rating for c in comps if c.rating is not None]
        feedbacks = [c.feedbacks for c in comps if c.feedbacks is not None]
        photos = [n for n in (_photo_n(c) for c in comps) if n is not None]

        market_position = "средний"

        if main.price is not None and prices:
            med = float(median(prices))
            cheaper = sum(1 for p in prices if p > main.price)
            dearer = sum(1 for p in prices if p < main.price)
            differences.append(
                f"Цена: наша {main.price} ₽, медиана конкурентов {int(round(med))} ₽"
            )
            if main.price < med * 0.92 or cheaper >= max(1, len(prices) // 2 + 1):
                market_position = "ниже рынка"
                advantages.append(
                    f"Цена ниже типичной по выборке (мы {main.price} ₽, медиана {int(round(med))} ₽)"
                )
            elif main.price > med * 1.08 or dearer >= max(1, len(prices) // 2 + 1):
                market_position = "выше рынка"
                problems.append(
                    f"Цена выше типичной по выборке (мы {main.price} ₽, медиана {int(round(med))} ₽)"
                )
                recommendations.append(
                    "Проверить цену относительно ближайших конкурентов или усилить УТП"
                )
            else:
                advantages.append("Цена в районе медианы конкурентов")
        elif main.price is None:
            problems.append("Нет данных о нашей цене для сравнения")
            recommendations.append("Подтянуть коммерческие поля карточки (цена)")

        if main.rating is not None and ratings:
            avg_r = sum(ratings) / len(ratings)
            differences.append(
                f"Рейтинг: наш {main.rating}, средний у конкурентов {avg_r:.2f}"
            )
            if main.rating + 0.05 < avg_r:
                problems.append(
                    f"Рейтинг ниже среднего по конкурентам ({main.rating} vs {avg_r:.2f})"
                )
                recommendations.append("Работать с качеством/ответами на отзывы для роста рейтинга")
            elif main.rating > avg_r + 0.05:
                advantages.append(
                    f"Рейтинг выше среднего по конкурентам ({main.rating} vs {avg_r:.2f})"
                )
            else:
                advantages.append("Рейтинг на уровне конкурентов")
        elif main.rating is None:
            problems.append("Нет данных о нашем рейтинге")

        if main.feedbacks is not None and feedbacks:
            avg_f = sum(feedbacks) / len(feedbacks)
            differences.append(
                f"Отзывы: наши {main.feedbacks}, среднее у конкурентов {avg_f:.0f}"
            )
            if main.feedbacks < avg_f * 0.7:
                problems.append(
                    f"Меньше отзывов, чем у типичного конкурента ({main.feedbacks} vs ~{avg_f:.0f})"
                )
                recommendations.append("Усилить сбор отзывов (послепродажные касания, QR/бонусы)")
            elif main.feedbacks > avg_f * 1.2:
                advantages.append(
                    f"Больше отзывов, чем у типичного конкурента ({main.feedbacks} vs ~{avg_f:.0f})"
                )
        elif main.feedbacks is None:
            problems.append("Нет данных о числе наших отзывов")

        main_photos = main.photo_count
        if main_photos is not None and photos:
            avg_p = sum(photos) / len(photos)
            differences.append(
                f"Фото: наши {main_photos}, среднее у конкурентов {avg_p:.1f}"
            )
            if main_photos + 0.5 < avg_p:
                problems.append(
                    f"Меньше фото, чем у конкурентов ({main_photos} vs ~{avg_p:.0f})"
                )
                recommendations.append("Добавить фото: ракурсы, размеры, использование, инфографика")
            elif main_photos > avg_p + 0.5:
                advantages.append(
                    f"Больше фото, чем у типичного конкурента ({main_photos} vs ~{avg_p:.0f})"
                )

        desc_flags = [_has_description(c) for c in comps]
        known_desc = [d for d in desc_flags if d is not None]
        if main.has_description is False and any(known_desc):
            problems.append("У конкурентов чаще заполнено описание")
            recommendations.append("Заполнить описание карточки (выгоды, состав, применение)")
            differences.append("Описание: у нас отсутствует, у части конкурентов есть")
        elif main.has_description is True:
            missing_comp = sum(1 for d in known_desc if d is False)
            if missing_comp:
                advantages.append("Описание заполнено лучше, чем у части конкурентов")
                differences.append(
                    f"Описание: у нас есть; отсутствует у {missing_comp} из {len(known_desc)} конкурентов"
                )

        if not comps:
            problems.append("Конкуренты не найдены — сравнение ограничено")
            recommendations.append("Уточнить категорию/ключевые слова для поиска конкурентов")
            market_position = "неизвестно"

        if not advantages and comps:
            advantages.append("Явных преимуществ по доступным метрикам не выявлено")
        if not recommendations and problems:
            recommendations.append("Сфокусироваться на закрытии проблем из сравнения с конкурентами")

        return CompetitorAnalysis(
            market_position=market_position,
            advantages=advantages,
            problems=problems,
            recommendations=recommendations,
            differences=differences,
        )
