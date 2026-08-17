"""
Competitor evidence → ARGUS diagnosis hints.

Не утверждает причинность продаж. Не советует «снизь цену» без unit economics.
"""

from __future__ import annotations

from typing import Any

from backend.competitor_intelligence.models import (
    CompetitiveDiagnosis,
    CompetitorComparison,
    PricePosition,
)


def _ri_has(review_assessment, *types: str) -> bool:
    if review_assessment is None:
        return False
    problems = list(getattr(review_assessment, "problems", None) or [])
    want = {t.upper() for t in types}
    for p in problems:
        st = getattr(p, "signal_type", None)
        name = getattr(st, "value", None) or str(st or "")
        freq = getattr(p, "frequency", None)
        freq_s = getattr(freq, "value", None) or str(freq or "")
        weak = bool((getattr(p, "metadata", None) or {}).get("weak"))
        if name.upper() in want and not weak and freq_s.upper() in ("HIGH", "MEDIUM", "RECURRING"):
            return True
        if name.upper() in want and not weak:
            return True
    return False


def _funnel_bits(funnel_diag: Any, seller_data: Any) -> tuple[str | None, str | None, float | None, float | None]:
    """Return (ctr_status, cvr_status, ctr, cvr) loosely."""
    ctr = getattr(seller_data, "ctr", None) if seller_data is not None else None
    cvr = getattr(seller_data, "cvr", None) if seller_data is not None else None
    try:
        ctr_f = float(ctr) if ctr is not None else None
    except (TypeError, ValueError):
        ctr_f = None
    try:
        cvr_f = float(cvr) if cvr is not None else None
    except (TypeError, ValueError):
        cvr_f = None
    ctr_low = ctr_f is not None and ctr_f < 2.0
    cvr_low = cvr_f is not None and cvr_f < 5.0
    ctr_ok = ctr_f is not None and ctr_f >= 2.0
    cvr_ok = cvr_f is not None and cvr_f >= 5.0
    if isinstance(funnel_diag, dict):
        case = str(funnel_diag.get("case") or funnel_diag.get("locus") or "")
        if "ENTRY" in case.upper() or case.upper() == "TRAFFIC":
            return ("low" if ctr_low else ctr_s_fallback(ctr_f)), (
                "ok" if cvr_ok else ("low" if cvr_low else None)
            ), ctr_f, cvr_f
    ctr_s = "low" if ctr_low else ("ok" if ctr_ok else None)
    cvr_s = "low" if cvr_low else ("ok" if cvr_ok else None)
    return ctr_s, cvr_s, ctr_f, cvr_f


def ctr_s_fallback(ctr_f: float | None) -> str | None:
    if ctr_f is None:
        return None
    return "low" if ctr_f < 2.0 else "ok"


def contribution_at_price(unit_econ: dict | None, target_price: float | None) -> float | None:
    if not unit_econ or not unit_econ.get("complete") or target_price is None:
        return None
    try:
        cost = float(unit_econ.get("cost") or 0)
        commission = float(unit_econ.get("commission") or 0)
        logistics = float(unit_econ.get("logistics") or 0)
        ads = float(unit_econ.get("ads_per_unit") or 0)
        returns_cost = float(unit_econ.get("returns_cost") or 0)
        return float(target_price) - cost - commission - logistics - ads - returns_cost
    except (TypeError, ValueError):
        return None


def diagnose_competitive(
    comparison: CompetitorComparison | None,
    *,
    review_assessment=None,
    funnel_diag=None,
    unit_econ: dict | None = None,
    seller_data=None,
    card_healthy: bool = False,
) -> CompetitiveDiagnosis:
    if comparison is None or comparison.sample_n <= 0:
        return CompetitiveDiagnosis(
            kind="insufficient",
            layer="CHECK",
            insight="Сопоставимые конкуренты не найдены — конкурентный вывод делать рано.",
            action_class="INSUFFICIENT_DATA",
            confidence=0.2,
            facts=[],
        )

    facts: list[str] = []
    if comparison.honesty_note:
        facts.append(comparison.honesty_note)

    if not comparison.sufficient_for_market:
        return CompetitiveDiagnosis(
            kind="insufficient",
            layer="OBSERVATION",
            insight=comparison.honesty_note
            or "Выборка слишком мала, чтобы говорить о рынке.",
            not_recommended=[
                "не писать «ты дороже рынка» по 1–2 слабым результатам",
                "не утверждать, что конкуренты получают больше продаж — данных о продажах конкурентов нет",
            ],
            price_position=PricePosition.UNKNOWN,
            confidence=0.25,
            facts=facts,
            action_class="INSUFFICIENT_DATA",
        )

    pos = comparison.price_position
    rating = comparison.rating
    fb = comparison.feedbacks
    photo_risk = _ri_has(review_assessment, "PHOTO_MATCH")
    quality_risk = _ri_has(
        review_assessment, "PRODUCT_QUALITY", "QUALITY", "SIZE", "PACKAGING",
    )
    ctr_s, cvr_s, ctr_f, cvr_f = _funnel_bits(funnel_diag, seller_data)

    seller_r = rating.seller
    med_r = rating.competitor_median
    rating_lower = (
        rating.sufficient
        and seller_r is not None
        and med_r is not None
        and seller_r + 0.15 < med_r
    )
    reviews_lower = (
        fb.sufficient
        and fb.seller is not None
        and fb.competitor_median is not None
        and fb.seller < fb.competitor_median * 0.4
    )

    if pos == PricePosition.MARKET_RANGE and (photo_risk or rating_lower):
        insight = (
            "Цена находится в рынке, но по доступным данным конкурентное преимущество "
            "по доверию слабое."
        )
        if photo_risk:
            insight += " Главная проблема вероятнее в доверии к товару/карточке, а не в цене."
        return CompetitiveDiagnosis(
            kind="trust_not_price",
            layer="OBSERVATION",
            insight=insight,
            action_hint=(
                "Сначала закрыть сигнал доверия (фото/соответствие/рейтинг), цену не трогать как главное действие."
            ),
            not_recommended=[
                "не снижать цену вместо исправления доверия/карточки",
                "не утверждать, что цена — причина падения продаж",
            ],
            price_position=pos,
            confidence=0.62,
            facts=facts + [
                f"позиция цены: {pos}",
                f"рейтинг продавца: {seller_r}" if seller_r is not None else "рейтинг продавца: UNKNOWN",
            ],
            action_class="TRUST",
        )

    target = comparison.price.competitor_median
    contrib_at = contribution_at_price(unit_econ, target)
    if (
        pos in (PricePosition.ABOVE_MARKET, PricePosition.MARKET_RANGE)
        and contrib_at is not None
        and contrib_at < 0
        and target is not None
    ):
        return CompetitiveDiagnosis(
            kind="unit_econ_block",
            layer="FACT",
            insight=(
                f"Конкуренты в выборке дешевле (медиана {target:.0f} ₽), "
                "но снижение до их цены сделает единичную прибыль отрицательной."
            ),
            action_hint="Не снижать цену до конкурентного уровня без изменения себестоимости.",
            not_recommended=[
                "цену не снижать до конкурентного уровня без изменения себестоимости",
            ],
            do_not_cut_price=True,
            price_position=pos,
            confidence=0.7,
            facts=facts + [
                f"вклад при цене конкурентов ≈ {contrib_at:.0f} ₽",
            ],
            action_class="NO_PRICE_CUT",
        )

    if pos == PricePosition.ABOVE_MARKET and ctr_s == "low" and cvr_s == "ok":
        return CompetitiveDiagnosis(
            kind="funnel_entry",
            layer="HYPOTHESIS",
            insight=(
                "Цена выше типичного диапазона выборки, CTR слабый, CVR стабилен — "
                "проблема вероятнее на входе, не только в карточке после клика."
            ),
            action_hint="Проверить превью/оффер и ценностный разрыв. Не делать вывод только из цены.",
            not_recommended=[
                "не утверждать, что цена является причиной падения продаж",
            ],
            price_position=pos,
            confidence=0.48,
            facts=facts + ([f"CTR={ctr_f}", f"CVR={cvr_f}"] if ctr_f is not None else []),
            action_class="CHECK",
        )

    if pos == PricePosition.ABOVE_MARKET and ctr_s == "ok" and cvr_s == "low":
        return CompetitiveDiagnosis(
            kind="funnel_after_click",
            layer="HYPOTHESIS",
            insight=(
                "Цена выше выборки, CTR стабилен, CVR ниже — цена может влиять уже после клика. "
                "Это гипотеза, не доказанная причина."
            ),
            action_hint="Проверить оффер/доверие/соответствие вместе с ценой. Не резать цену вслепую.",
            not_recommended=[
                "не утверждать, что цена является единственной причиной низкой CVR",
            ],
            price_position=pos,
            confidence=0.48,
            facts=facts,
            action_class="CHECK",
        )

    no_major_ri = not photo_risk and not quality_risk
    rating_ok = (not rating.sufficient) or (
        seller_r is not None and med_r is not None and abs(seller_r - med_r) <= 0.25
    )
    reviews_ok = (not fb.sufficient) or not reviews_lower
    if (
        pos == PricePosition.ABOVE_MARKET
        and no_major_ri
        and rating_ok
        and reviews_ok
        and (ctr_s == "low" or ctr_s is None)
    ):
        return CompetitiveDiagnosis(
            kind="price_candidate",
            layer="HYPOTHESIS",
            insight=(
                "Цена выше типичного диапазона выборки, рейтинг/отзывы сопоставимы, "
                "крупного RI-риска нет — цена становится кандидатом на проверку, не доказанной причиной."
            ),
            action_hint="Проверить ценностный разрыв и юнит-экономику. Не снижать цену автоматически.",
            not_recommended=[
                "не писать «снизь цену», пока не проверена маржа",
                "не утверждать, что конкуренты получают больше продаж",
            ],
            price_position=pos,
            confidence=0.45,
            facts=facts,
            action_class="CHECK",
        )

    if card_healthy and pos in (PricePosition.MARKET_RANGE, PricePosition.BELOW_MARKET) and no_major_ri:
        return CompetitiveDiagnosis(
            kind="no_action",
            layer="OBSERVATION",
            insight=(
                "По доступным данным товар в рынке, системного конкурентного проигрыша не видно."
            ),
            action_hint="Ничего критичного в цене/карточке не менять без нового сигнала.",
            not_recommended=[
                "не переписывать здоровую карточку и не трогать цену без сигнала",
            ],
            price_position=pos,
            confidence=0.55,
            facts=facts,
            action_class="NO_ACTION",
        )

    if pos == PricePosition.BELOW_MARKET:
        return CompetitiveDiagnosis(
            kind="below_market",
            layer="OBSERVATION",
            insight="Цена ниже типичного диапазона выборки — само по себе это не доказательство роста продаж.",
            not_recommended=[
                "не утверждать, что низкая цена даёт больше продаж конкурентам или вам",
            ],
            price_position=pos,
            confidence=0.4,
            facts=facts,
            action_class="CHECK",
        )

    return CompetitiveDiagnosis(
        kind="in_market",
        layer="OBSERVATION",
        insight="По доступной выборке цена в диапазоне рынка. Одного фактора недостаточно для диагноза.",
        not_recommended=[
            "не менять цену без ценностного разрыва и данных воронки",
        ],
        price_position=pos,
        confidence=0.4,
        facts=facts,
        action_class="CHECK",
    )
