"""
recommendations.py — правило-based рекомендации для продавца.

Два публичных метода:

  generate(product)
      Строковые рекомендации по карточке товара (существующая логика,
      не меняется).

  generate_market_recommendations(context)
      Структурированные рекомендации на основе CategoryContext из
      Intelligence Layer. Работает только при наличии накопленных данных:
      сигналы с низкой confidence или одиночные слабые источники дают
      MONITOR вместо конкретных действий.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from backend.wb.models import WBProduct

# ─── типы рекомендаций ────────────────────────────────────────────────── #


class RecommendationType(str, Enum):
    PRICE       = "PRICE"       # ценовое действие
    ADVERTISING = "ADVERTISING" # управление рекламой
    CONTENT     = "CONTENT"     # контент карточки
    STOCK       = "STOCK"       # управление запасами
    MARKET      = "MARKET"      # рыночная информация
    MONITOR     = "MONITOR"     # следить — данных ещё недостаточно


@dataclass
class Recommendation:
    """
    Структурированная рекомендация для продавца.

    evidence_ids — id Evidence, на которых строится вывод.
                   Всегда непустой список; MONITOR без данных
                   содержит специальный маркер "no_evidence".
    priority     — 1 (срочно) … 5 (наблюдать).
    confidence   — уверенность вывода (0.0 … 1.0).
    """

    type:         RecommendationType
    title:        str
    reason:       str
    action:       str
    confidence:   float
    evidence_ids: list[str]
    priority:     int           # 1 = высокий, 5 = низкий


# ─── пороги ──────────────────────────────────────────────────────────── #

_MIN_CONFIDENCE   = 0.50   # ниже — не строим конкретную рекомендацию
_STRONG_CONFIDENCE = 0.65  # выше — можно рекомендовать конкретное действие
_HIGH_DEMAND_FP   = 1_000_000  # found_phrase: высокий спрос


# ─── RecommendationGenerator ──────────────────────────────────────────── #


class RecommendationGenerator:
    """
    outcome_tracker — опциональный OutcomeTracker.
    На v1 НЕ вызывается автоматически: только безопасный DI-хук.
    Если None — старый flow без изменений.
    """

    def __init__(self, outcome_tracker=None) -> None:
        self._outcome_tracker = outcome_tracker

    def generate(self, product: WBProduct):
        """
        Рекомендации по карточке — только при реальном основании (порог/пробел).
        Без универсальных «улучшайте качество» / «собирайте отзывы».
        Пустой список, если оснований нет.
        """
        recommendations = []

        n_photos = len(product.photos or [])
        if n_photos < 5:
            recommendations.append(
                "📸 Добавьте больше фотографий товара (желательно 8–12)."
            )
            recommendations.append(
                "🎨 Сделайте инфографику на первых изображениях."
            )
        elif n_photos < 8:
            recommendations.append(
                "📸 Добавьте больше фотографий товара (желательно 8–12)."
            )

        desc = product.description or ""
        if not desc:
            recommendations.append("📝 Добавьте подробное описание товара.")
        elif len(desc) < 120:
            recommendations.append(
                f"📝 Сделайте описание более подробным (сейчас {len(desc)} символов)."
            )

        # Характеристики: только если реально мало (не универсальный <15).
        n_chars = len(product.characteristics or {})
        if n_chars == 0:
            recommendations.append("📋 Заполните характеристики товара.")
        elif n_chars < 5:
            recommendations.append(
                f"📋 Заполните больше характеристик (сейчас {n_chars})."
            )

        # Цена отсутствует — честный пробел, не выдумываем.
        if product.price is None:
            recommendations.append(
                "💰 Публичная цена не получена — укажите недостающее в точном анализе."
            )

        # Рейтинг/отзывы: без шаблонных «улучшайте качество» / «собирайте отзывы».
        # Низкий рейтинг — факт для Advisor/RI, не универсальная рекомендация здесь.

        return recommendations

    # ──────────────────────────────────────────────────────────────────── #
    #  Intelligence-based structured recommendations                       #
    # ──────────────────────────────────────────────────────────────────── #

    def generate_market_recommendations(self, context) -> list[Recommendation]:
        """
        Структурированные рекомендации на основе CategoryContext.

        context — backend.intelligence.category_intelligence.CategoryContext
                  (принимаем как Any для развязки импортов; достаточно
                   duck-typing при обращении к .demand_signals, .trend_signals и т.д.)

        Возвращает список Recommendation, отсортированный по priority.
        Никогда не выбрасывает исключение — возвращает [] при любой ошибке.
        """
        if context is None:
            return []

        try:
            return self._build_market_recs(context)
        except Exception:
            return []

    def _build_market_recs(self, context) -> list[Recommendation]:
        recs: list[Recommendation] = []

        # ── 1. Сбор сигналов ──────────────────────────────────────────── #
        demand_items   = list(getattr(context, "demand_signals", []) or [])
        trend_signals  = list(getattr(context, "trend_signals",  []) or [])
        seasonal       = dict(getattr(context, "seasonal_signals", {}) or {})
        market_events  = list(getattr(context, "market_events",  []) or [])
        evidences      = list(getattr(context, "evidence",       []) or [])
        ctx_confidence = float(getattr(context, "confidence",    0.0))
        category       = str(getattr(context, "category", ""))

        # Evidence с достаточной уверенностью (только они идут в evidence_ids)
        strong_ev = [e for e in evidences if getattr(e, "confidence", 0) >= _MIN_CONFIDENCE]
        strong_ev_ids = [e.id for e in strong_ev]

        # ── 2. Недостаточно данных → только MONITOR ───────────────────── #
        # Сезонные сигналы тоже считаются данными — не делаем ранний выход.
        has_any_data = (
            demand_items or trend_signals or market_events
            or evidences or seasonal
        )
        if not has_any_data:
            recs.append(Recommendation(
                type=RecommendationType.MONITOR,
                title="Нет рыночных данных",
                reason="Intelligence Layer ещё не накопил данные по этой категории.",
                action="Дождитесь накопления данных и повторите анализ позже.",
                confidence=0.0,
                evidence_ids=["no_evidence"],
                priority=5,
            ))
            return recs

        # ── 3. Высокий спрос + тренд → ADVERTISING / STOCK ──────────────── #
        high_demand_items = [
            d for d in demand_items
            if (d.metadata or {}).get("found_phrase", 0) >= _HIGH_DEMAND_FP
        ]
        trend_up = [
            t for t in trend_signals
            if getattr(t, "direction", None) is not None
            and getattr(t.direction, "value", str(t.direction)) == "up"
            and getattr(t, "confidence", 0) >= _MIN_CONFIDENCE
        ]
        trend_down = [
            t for t in trend_signals
            if getattr(t, "direction", None) is not None
            and getattr(t.direction, "value", str(t.direction)) == "down"
            and getattr(t, "confidence", 0) >= _MIN_CONFIDENCE
        ]

        # Спрос высокий И тренд подтверждён
        if high_demand_items and trend_up:
            combined_ev_ids = strong_ev_ids + [t.id for t in trend_up[:2]]
            best_conf = max(
                [getattr(t, "confidence", 0) for t in trend_up],
                default=0,
            )
            if best_conf >= _STRONG_CONFIDENCE:
                recs.append(Recommendation(
                    type=RecommendationType.ADVERTISING,
                    title=f"Категория «{category}» — растущий спрос",
                    reason=(
                        f"Зафиксированы высокий спрос (>1 млн запросов) "
                        f"и восходящий тренд (confidence {best_conf:.0%})."
                    ),
                    action=(
                        "Рассмотрите увеличение рекламного бюджета в этой категории, "
                        "пока спрос на подъёме."
                    ),
                    confidence=best_conf,
                    evidence_ids=combined_ev_ids[:6],
                    priority=1,
                ))
            else:
                # Тренд есть, но уверенность невысокая → MONITOR
                recs.append(Recommendation(
                    type=RecommendationType.MONITOR,
                    title=f"Возможный рост спроса в «{category}»",
                    reason="Зафиксирован спрос и тренд, но уверенность сигналов невысокая.",
                    action="Следите за динамикой: если тренд подтвердится — усильте рекламу.",
                    confidence=best_conf,
                    evidence_ids=combined_ev_ids[:4] or ["no_evidence"],
                    priority=3,
                ))

        # Только высокий спрос, без подтверждённого тренда → STOCK + MONITOR
        elif high_demand_items and not trend_up and not trend_down:
            ev_ids = [d.id for d in high_demand_items[:3]] + strong_ev_ids[:2]
            recs.append(Recommendation(
                type=RecommendationType.STOCK,
                title=f"Высокий спрос в категории «{category}»",
                reason="Зафиксирован высокий объём поисковых запросов в категории.",
                action="Убедитесь, что у вас достаточный запас товара для удовлетворения спроса.",
                confidence=min(ctx_confidence + 0.10, 0.75),
                evidence_ids=ev_ids[:4] or ["no_evidence"],
                priority=2,
            ))

        # Тренд вниз → MONITOR
        if trend_down and not trend_up:
            best_down = max(
                [getattr(t, "confidence", 0) for t in trend_down],
                default=0,
            )
            recs.append(Recommendation(
                type=RecommendationType.MONITOR,
                title=f"Снижение спроса в «{category}»",
                reason=f"Зафиксирован нисходящий тренд (confidence {best_down:.0%}).",
                action=(
                    "Снизьте рекламный бюджет. "
                    "Проанализируйте ценовую позицию относительно конкурентов."
                ),
                confidence=best_down,
                evidence_ids=[t.id for t in trend_down[:3]],
                priority=2,
            ))

        # ── 4. Сезонность ─────────────────────────────────────────────── #
        if seasonal:
            import datetime
            cur_month = datetime.datetime.utcnow().month
            idx = seasonal.get(cur_month)
            if idx is not None:
                if idx >= 1.20 and ctx_confidence >= _MIN_CONFIDENCE:
                    recs.append(Recommendation(
                        type=RecommendationType.ADVERTISING,
                        title="Сезонный пик продаж",
                        reason=(
                            f"Сезонный индекс спроса в текущем месяце: {idx:.2f} "
                            f"(выше нормы). Категория: «{category}»."
                        ),
                        action=(
                            "Усильте рекламу и пополните остатки: "
                            "сезонный пик — лучшее время для роста продаж."
                        ),
                        confidence=min(ctx_confidence + 0.05, 0.90),
                        evidence_ids=strong_ev_ids[:3] or ["no_evidence"],
                        priority=1,
                    ))
                elif idx <= 0.80 and ctx_confidence >= _MIN_CONFIDENCE:
                    recs.append(Recommendation(
                        type=RecommendationType.MONITOR,
                        title="Сезонный спад",
                        reason=(
                            f"Сезонный индекс в текущем месяце: {idx:.2f} "
                            f"(ниже нормы). Категория: «{category}»."
                        ),
                        action="Сократите рекламный бюджет до восстановления спроса.",
                        confidence=min(ctx_confidence + 0.05, 0.85),
                        evidence_ids=strong_ev_ids[:3] or ["no_evidence"],
                        priority=3,
                    ))

        # ── 5. Рыночные события ───────────────────────────────────────── #
        from backend.intelligence.models import EventType

        sale_events = [
            e for e in market_events
            if getattr(e, "event_type", None) == EventType.SALE
            and getattr(e, "confidence", 0) >= _MIN_CONFIDENCE
        ]
        regulation_events = [
            e for e in market_events
            if getattr(e, "event_type", None) == EventType.REGULATION
            and getattr(e, "confidence", 0) >= _MIN_CONFIDENCE
        ]
        competitor_events = [
            e for e in market_events
            if getattr(e, "event_type", None) == EventType.COMPETITOR
            and getattr(e, "confidence", 0) >= _MIN_CONFIDENCE
        ]

        if sale_events:
            best_ev = max(sale_events, key=lambda e: getattr(e, "confidence", 0))
            recs.append(Recommendation(
                type=RecommendationType.PRICE,
                title="Акция / распродажа в категории",
                reason=(
                    f"Обнаружено событие: «{getattr(best_ev, 'title', '')[:80]}». "
                    f"Возможно, конкуренты снижают цены."
                ),
                action=(
                    "Проверьте цены конкурентов. "
                    "Рассмотрите участие в акции для поддержания позиций."
                ),
                confidence=getattr(best_ev, "confidence", 0.50),
                evidence_ids=[e.id for e in sale_events[:3]],
                priority=2,
            ))

        if regulation_events:
            best_ev = max(regulation_events, key=lambda e: getattr(e, "confidence", 0))
            recs.append(Recommendation(
                type=RecommendationType.MARKET,
                title="Регуляторное изменение в категории",
                reason=f"Событие: «{getattr(best_ev, 'title', '')[:80]}».",
                action=(
                    "Ознакомьтесь с новыми требованиями. "
                    "Убедитесь, что ваш товар им соответствует."
                ),
                confidence=getattr(best_ev, "confidence", 0.50),
                evidence_ids=[e.id for e in regulation_events[:3]],
                priority=2,
            ))

        if competitor_events:
            recs.append(Recommendation(
                type=RecommendationType.MONITOR,
                title="Активность конкурентов",
                reason=f"Зафиксированы изменения у конкурентов в категории «{category}».",
                action="Проанализируйте карточки лидеров категории.",
                confidence=max(
                    (getattr(e, "confidence", 0) for e in competitor_events),
                    default=0.40,
                ),
                evidence_ids=[e.id for e in competitor_events[:3]],
                priority=4,
            ))

        # ── 6. Конфликтующие Evidence → осторожная рекомендация ──────── #
        if trend_up and trend_down:
            recs.append(Recommendation(
                type=RecommendationType.MONITOR,
                title="Противоречивые сигналы тренда",
                reason=(
                    "Одновременно зафиксированы восходящий и нисходящий тренды. "
                    "Данные могут отражать разные временные периоды."
                ),
                action="Дождитесь большего накопления данных перед принятием решений.",
                confidence=_MIN_CONFIDENCE,
                evidence_ids=(
                    [t.id for t in trend_up[:2]]
                    + [t.id for t in trend_down[:2]]
                ),
                priority=3,
            ))

        # ── 7. Общий слабый сигнал → MONITOR ─────────────────────────── #
        if not recs and ctx_confidence < _MIN_CONFIDENCE:
            recs.append(Recommendation(
                type=RecommendationType.MONITOR,
                title="Недостаточно данных для рекомендации",
                reason=(
                    f"Общий confidence контекста ({ctx_confidence:.0%}) "
                    f"ниже порога {_MIN_CONFIDENCE:.0%}."
                ),
                action="Дождитесь накопления большего количества рыночных данных.",
                confidence=ctx_confidence,
                evidence_ids=strong_ev_ids[:2] or ["no_evidence"],
                priority=5,
            ))

        recs.sort(key=lambda r: r.priority)
        return recs

    # ──────────────────────────────────────────────────────────────────── #
    #  Review Intelligence recommendations                                 #
    # ──────────────────────────────────────────────────────────────────── #

    def generate_review_recommendations(self, assessment) -> list[Recommendation]:
        """
        Рекомендации на основе ReviewAssessment.
        Один слабый отзыв → без ACTION (только MONITOR при необходимости).
        """
        if assessment is None:
            return []
        try:
            return self._build_review_recs(assessment)
        except Exception:
            return []

    def _build_review_recs(self, assessment) -> list[Recommendation]:
        from backend.intelligence.reviews import ReviewIntelligence

        # Предпочитаем готовые SellerAction из ReviewIntelligence
        actions = list(getattr(assessment, "actions", None) or [])
        if actions:
            return self._recs_from_seller_actions(actions, assessment)

        recs: list[Recommendation] = []
        recurring = ReviewIntelligence.recurring_issues(assessment)
        if not recurring:
            if getattr(assessment, "processed_count", 0) > 0:
                recs.append(Recommendation(
                    type=RecommendationType.MONITOR,
                    title="Отзывы без подтверждённого паттерна",
                    reason="Есть отзывы, но recurring issue не достиг порога.",
                    action="Соберите больше отзывов перед изменениями карточки.",
                    confidence=float(getattr(assessment, "confidence", 0.0) or 0.0),
                    evidence_ids=["no_recurring_issue"],
                    priority=5,
                ))
            return recs

        for issue in recurring:
            sentiment = getattr(issue.sentiment, "value", "")
            if sentiment not in ("NEGATIVE", "UNKNOWN"):
                continue
            stype = getattr(issue.signal_type, "value", str(issue.signal_type))
            if sentiment == "UNKNOWN" and stype not in (
                "PACKAGING", "DAMAGE", "FUNCTIONALITY", "SIZE",
                "APPEARANCE", "QUALITY", "PRICE_VALUE", "DELIVERY",
            ):
                continue
            ids = list(getattr(issue, "source_ids", None) or [issue.id])
            conf = float(issue.confidence)
            claim = (issue.claim or "")[:120]

            if stype in ("PACKAGING", "DAMAGE", "UNPACKING"):
                recs.append(Recommendation(
                    type=RecommendationType.CONTENT,
                    title="Улучшить упаковку",
                    reason=f"Повторяющиеся жалобы на упаковку/повреждения: {claim}",
                    action="Усильте упаковку и контроль отгрузки; зафиксируйте изменения.",
                    confidence=conf,
                    evidence_ids=ids[:8],
                    priority=1,
                ))
            elif stype == "COMPLETENESS":
                recs.append(Recommendation(
                    type=RecommendationType.CONTENT,
                    title="Прояснить комплектацию",
                    reason=f"Повторяющиеся жалобы на комплектацию: {claim}",
                    action="Добавьте инструкцию и понятную раскладку комплекта.",
                    confidence=conf,
                    evidence_ids=ids[:8],
                    priority=2,
                ))
            elif stype == "FUNCTIONALITY":
                recs.append(Recommendation(
                    type=RecommendationType.CONTENT,
                    title="Проверить качество и характеристики",
                    reason=f"Повторяющиеся жалобы на функциональность: {claim}",
                    action="Проверьте партию/характеристики и обновите описание ограничений.",
                    confidence=conf,
                    evidence_ids=ids[:8],
                    priority=1,
                ))
            elif stype in ("APPEARANCE", "PHOTO_MATCH", "DESIGN"):
                recs.append(Recommendation(
                    type=RecommendationType.CONTENT,
                    title="Сверить фото с реальным товаром",
                    reason=f"Повторяющиеся жалобы на внешний вид: {claim}",
                    action="Обновите фотографии так, чтобы они соответствовали реальному товару.",
                    confidence=conf,
                    evidence_ids=ids[:8],
                    priority=2,
                ))
            elif stype == "DESCRIPTION_MATCH":
                recs.append(Recommendation(
                    type=RecommendationType.CONTENT,
                    title="Сверить описание с товаром",
                    reason=f"Повторяющиеся жалобы на описание: {claim}",
                    action="Приведите характеристики и описание в соответствие с фактом.",
                    confidence=conf,
                    evidence_ids=ids[:8],
                    priority=2,
                ))
            elif stype == "SIZE":
                recs.append(Recommendation(
                    type=RecommendationType.CONTENT,
                    title="Добавить точные размеры",
                    reason=f"Повторяющиеся жалобы на размер: {claim}",
                    action="Добавьте таблицу размеров и точные замеры в карточку.",
                    confidence=conf,
                    evidence_ids=ids[:8],
                    priority=2,
                ))
            elif stype == "PRICE_VALUE":
                recs.append(Recommendation(
                    type=RecommendationType.MARKET,
                    title="Пересмотреть perceived value",
                    reason=f"Повторяющиеся замечания о цене/ценности: {claim}",
                    action=(
                        "Усильте аргументы ценности в описании и контенте. "
                        "Новую цену не назначайте без отдельных данных."
                    ),
                    confidence=conf,
                    evidence_ids=ids[:8],
                    priority=3,
                ))
            elif stype in ("QUALITY", "PRODUCT_QUALITY"):
                recs.append(Recommendation(
                    type=RecommendationType.CONTENT,
                    title="Разобрать жалобы на качество",
                    reason=f"Повторяющиеся жалобы на качество: {claim}",
                    action="Проверьте поставщика/партию и отразите ключевые свойства в карточке.",
                    confidence=conf,
                    evidence_ids=ids[:8],
                    priority=1,
                ))
            elif stype in ("DELIVERY", "LOGISTICS"):
                recs.append(Recommendation(
                    type=RecommendationType.MONITOR,
                    title="Проблемы доставки в отзывах",
                    reason=f"Повторяющиеся жалобы на доставку: {claim}",
                    action="Проверьте схему отгрузки и сроки; часть сигналов может быть от WB.",
                    confidence=conf,
                    evidence_ids=ids[:8],
                    priority=3,
                ))
            elif stype == "EXPECTATIONS":
                recs.append(Recommendation(
                    type=RecommendationType.CONTENT,
                    title="Снизить разрыв ожиданий",
                    reason=f"Повторяющиеся сигналы об ожиданиях: {claim}",
                    action="Честно опишите в карточке, что получит покупатель.",
                    confidence=conf,
                    evidence_ids=ids[:8],
                    priority=3,
                ))

        recs.sort(key=lambda r: r.priority)
        return recs

    def _recs_from_seller_actions(self, actions, assessment) -> list[Recommendation]:
        """SellerAction → Recommendation; без выдуманных чисел."""
        recs: list[Recommendation] = []
        # Не более одной MONITOR-осторожной, если нет сильных
        strong = [a for a in actions if a.priority <= 3 and a.evidence_ids]
        weak = [a for a in actions if a.priority >= 4]

        source = strong if strong else weak[:1]
        if not source and getattr(assessment, "processed_count", 0) > 0:
            return [Recommendation(
                type=RecommendationType.MONITOR,
                title="Отзывы без подтверждённого паттерна",
                reason="Есть отзывы, но recurring issue не достиг порога.",
                action="Соберите больше отзывов перед изменениями карточки.",
                confidence=float(getattr(assessment, "confidence", 0.0) or 0.0),
                evidence_ids=["no_recurring_issue"],
                priority=5,
            )]

        seen_titles: set[str] = set()
        for a in source:
            if a.title in seen_titles:
                continue
            seen_titles.add(a.title)
            st = getattr(getattr(a, "signal_type", None), "value", "") or ""
            if a.priority >= 4:
                rtype = RecommendationType.MONITOR
                prio = 5
            elif st == "PRICE_VALUE":
                rtype = RecommendationType.MARKET
                prio = max(1, min(5, int(a.priority)))
            elif st == "DELIVERY" or st == "LOGISTICS":
                rtype = RecommendationType.MONITOR
                prio = max(1, min(5, int(a.priority)))
            else:
                rtype = RecommendationType.CONTENT
                prio = max(1, min(5, int(a.priority)))

            action_text = a.title
            if st == "PRICE_VALUE":
                action_text = (
                    f"{a.title}. Новую цену не назначайте без отдельных данных."
                )

            ids = list(a.evidence_ids or [])
            if not ids:
                continue
            recs.append(Recommendation(
                type=rtype,
                title=a.title,
                reason=(a.rationale or "")[:200],
                action=action_text,
                confidence=float(a.confidence),
                evidence_ids=ids[:8],
                priority=prio,
            ))

        recs.sort(key=lambda r: r.priority)
        return recs