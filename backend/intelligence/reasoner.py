"""
reasoner.py — Intelligence Reasoner v1.

Rule-based рассуждение поверх IntelligenceSnapshot.

IntelligenceReasoner.reason(snapshot) → IntelligenceAssessment

Принципы:
    - Только данные из snapshot, никакого HTTP.
    - Каждый вывод подкреплён evidence_ids.
    - Конфликтующие сигналы не скрываются — помечаются явно.
    - При недостатке уверенности → "Недостаточно данных".
    - Корреляция ≠ причинность: выводы аккуратно сформулированы.
    - FACT / OBSERVATION / INFERENCE строго разделены в claim.

Правила рассуждения:
    HIGH_DEMAND + UP_TREND          → opportunity
    HIGH_DEMAND + DOWN_TREND        → risk
    SEASONAL_PEAK + HIGH_DEMAND     → opportunity
    SEASONAL_DROP + DOWN_TREND      → risk
    SALE + HIGH_DEMAND              → opportunity (реклама/остатки)
    REGULATION                      → risk
    COMPETITOR                      → monitor
    UP + DOWN conflict              → risk/uncertainty
    LOW_CONFIDENCE                  → "Недостаточно данных"
"""

from __future__ import annotations

import calendar
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from backend.intelligence.catalog import IntelligenceSnapshot
from backend.intelligence.models import EventType, TrendDirection

log = logging.getLogger("selleros.intelligence.reasoner")

# ─── пороги ─────────────────────────────────────────────────────────── #

_MIN_REASON_CONFIDENCE: float = 0.35   # ниже → "недостаточно данных"
_HIGH_DEMAND_THRESHOLD: int   = 2      # >= N demand items → HIGH_DEMAND
_SEASONAL_PEAK_THRESHOLD: float = 1.20 # demand_index >= этого → PEAK
_SEASONAL_DROP_THRESHOLD: float = 0.80 # demand_index <= этого → DROP


# ─── сигналы ────────────────────────────────────────────────────────── #

class DemandSignal(str, Enum):
    HIGH    = "HIGH"
    MEDIUM  = "MEDIUM"
    LOW     = "LOW"
    UNKNOWN = "UNKNOWN"


class TrendSignal(str, Enum):
    UP       = "UP"
    DOWN     = "DOWN"
    STABLE   = "STABLE"
    CONFLICT = "CONFLICT"  # одновременно UP и DOWN
    UNKNOWN  = "UNKNOWN"


class SeasonalitySignal(str, Enum):
    PEAK    = "PEAK"    # сейчас пик сезона
    DROP    = "DROP"    # сейчас спад сезона
    NORMAL  = "NORMAL"
    UNKNOWN = "UNKNOWN"


class EventSignal(str, Enum):
    SALE        = "SALE"
    REGULATION  = "REGULATION"
    COMPETITOR  = "COMPETITOR"
    PLATFORM    = "PLATFORM"
    ECONOMIC    = "ECONOMIC"
    HOLIDAY     = "HOLIDAY"
    NONE        = "NONE"


# ─── вывод ──────────────────────────────────────────────────────────── #

class ConclusionType(str, Enum):
    OPPORTUNITY = "OPPORTUNITY"
    RISK        = "RISK"
    MONITOR     = "MONITOR"
    FACT        = "FACT"
    INFERENCE   = "INFERENCE"
    STRENGTH    = "STRENGTH"


@dataclass
class Conclusion:
    """Единичный вывод рассуждателя."""

    type: ConclusionType
    claim: str               # Краткое утверждение для LLM
    rationale: str           # Почему такой вывод
    confidence: float        # 0..1
    evidence_ids: list[str]  = field(default_factory=list)
    is_conflict: bool        = False


# ─── оценка ─────────────────────────────────────────────────────────── #

@dataclass
class IntelligenceAssessment:
    """
    Результат рассуждения над IntelligenceSnapshot.

    Поля:
        demand_signal       — оценка уровня спроса
        trend_signal        — направление тренда (с обнаружением конфликта)
        seasonality_signal  — текущий сезонный сигнал
        event_signal        — наиболее значимый рыночный сигнал
        conclusions         — список выводов (OPPORTUNITY / RISK / MONITOR)
        risks               — выводы типа RISK
        opportunities       — выводы типа OPPORTUNITY
        supporting_evidence_ids — все evidence_ids, задействованные в рассуждении
        overall_confidence  — сводный confidence оценки (0..1)
        generated_at        — unix timestamp
    """

    category: str | None
    region: str

    demand_signal:      DemandSignal      = DemandSignal.UNKNOWN
    trend_signal:       TrendSignal       = TrendSignal.UNKNOWN
    seasonality_signal: SeasonalitySignal = SeasonalitySignal.UNKNOWN
    event_signal:       EventSignal       = EventSignal.NONE

    conclusions:             list[Conclusion] = field(default_factory=list)
    risks:                   list[Conclusion] = field(default_factory=list)
    opportunities:           list[Conclusion] = field(default_factory=list)
    supporting_evidence_ids: list[str]        = field(default_factory=list)

    overall_confidence: float = 0.0
    generated_at: float = field(default_factory=time.time)


# ─── IntelligenceReasoner ─────────────────────────────────────────── #

class IntelligenceReasoner:
    """
    Rule-based рассуждатель над IntelligenceSnapshot.

    Не делает HTTP-запросов.
    Не изменяет IntelligenceCatalog или CategoryIntelligence.
    """

    async def reason(self, snapshot: IntelligenceSnapshot) -> IntelligenceAssessment:
        """
        Построить IntelligenceAssessment из snapshot.

        При любой ошибке — возвращает пустую оценку с overall_confidence=0.
        """
        try:
            return self._reason_sync(snapshot)
        except Exception as exc:
            log.error("IntelligenceReasoner.reason error: %s", exc, exc_info=True)
            return IntelligenceAssessment(
                category=snapshot.category,
                region=snapshot.region,
                generated_at=time.time(),
            )

    # ─── внутренняя логика ─────────────────────────────────────────── #

    def _reason_sync(self, snapshot: IntelligenceSnapshot) -> IntelligenceAssessment:
        all_ev_ids = [e.id for e in snapshot.evidence]

        # 1. Определить сигналы
        demand_sig    = _assess_demand(snapshot)
        trend_sig     = _assess_trend(snapshot)
        seasonal_sig  = _assess_seasonality(snapshot)
        event_sig     = _assess_event(snapshot)

        # 2. Проверить достаточность данных
        review_conclusions = _rule_review_issues(snapshot)
        if snapshot.confidence < _MIN_REASON_CONFIDENCE and not review_conclusions:
            conclusion = Conclusion(
                type=ConclusionType.MONITOR,
                claim="Недостаточно данных для обоснованных выводов.",
                rationale=(
                    f"Сводный confidence среза {snapshot.confidence:.0%} "
                    f"ниже порога {_MIN_REASON_CONFIDENCE:.0%}."
                ),
                confidence=snapshot.confidence,
                evidence_ids=all_ev_ids,
            )
            return IntelligenceAssessment(
                category=snapshot.category,
                region=snapshot.region,
                demand_signal=demand_sig,
                trend_signal=trend_sig,
                seasonality_signal=seasonal_sig,
                event_signal=event_sig,
                conclusions=[conclusion],
                overall_confidence=snapshot.confidence,
                generated_at=time.time(),
            )

        # 3. Построить выводы по правилам
        conclusions: list[Conclusion] = []

        conclusions += _rule_high_demand_up_trend(snapshot, demand_sig, trend_sig)
        conclusions += _rule_high_demand_down_trend(snapshot, demand_sig, trend_sig)
        conclusions += _rule_seasonal_peak(snapshot, demand_sig, seasonal_sig)
        conclusions += _rule_seasonal_drop(snapshot, trend_sig, seasonal_sig)
        conclusions += _rule_sale(snapshot, demand_sig, event_sig)
        conclusions += _rule_regulation(snapshot, event_sig)
        conclusions += _rule_competitor(snapshot, event_sig)
        conclusions += _rule_conflict(snapshot, trend_sig)
        conclusions += _rule_economic(snapshot, event_sig)
        conclusions += review_conclusions

        # Если ни один вывод не сработал — явное MONITOR
        if not conclusions:
            conclusions.append(Conclusion(
                type=ConclusionType.MONITOR,
                claim="Сигналы присутствуют, но однозначных паттернов не обнаружено.",
                rationale="Ни одно из известных правил рассуждения не применимо к текущему срезу.",
                confidence=snapshot.confidence * 0.7,
                evidence_ids=all_ev_ids[:5],
            ))

        # 4. Сортировка: RISK → STRENGTH/OPPORTUNITY → MONITOR
        conclusions.sort(
            key=lambda c: {"RISK": 0, "STRENGTH": 1, "OPPORTUNITY": 1}.get(c.type.value, 2)
        )

        risks         = [c for c in conclusions if c.type == ConclusionType.RISK]
        opportunities = [c for c in conclusions if c.type == ConclusionType.OPPORTUNITY]

        # 5. Собрать уникальные evidence_ids
        used_ids: list[str] = []
        seen: set[str] = set()
        for c in conclusions:
            for eid in c.evidence_ids:
                if eid not in seen:
                    seen.add(eid)
                    used_ids.append(eid)

        overall = _overall_confidence(snapshot, conclusions)

        log.info(
            "IntelligenceReasoner: cat=%r dem=%s trn=%s sea=%s evt=%s "
            "conclusions=%d conf=%.2f",
            snapshot.category, demand_sig.value, trend_sig.value,
            seasonal_sig.value, event_sig.value,
            len(conclusions), overall,
        )

        return IntelligenceAssessment(
            category=snapshot.category,
            region=snapshot.region,
            demand_signal=demand_sig,
            trend_signal=trend_sig,
            seasonality_signal=seasonal_sig,
            event_signal=event_sig,
            conclusions=conclusions,
            risks=risks,
            opportunities=opportunities,
            supporting_evidence_ids=used_ids,
            overall_confidence=overall,
            generated_at=time.time(),
        )


# ─── signal assessors ────────────────────────────────────────────── #

def _assess_demand(snap: IntelligenceSnapshot) -> DemandSignal:
    n = len(snap.demand)
    if n >= _HIGH_DEMAND_THRESHOLD:
        return DemandSignal.HIGH
    if n == 1:
        return DemandSignal.MEDIUM
    return DemandSignal.LOW if snap.evidence else DemandSignal.UNKNOWN


def _assess_trend(snap: IntelligenceSnapshot) -> TrendSignal:
    if not snap.trends:
        return TrendSignal.UNKNOWN
    directions = {tr.direction for tr in snap.trends}
    if TrendDirection.UP in directions and TrendDirection.DOWN in directions:
        return TrendSignal.CONFLICT
    if TrendDirection.UP in directions:
        return TrendSignal.UP
    if TrendDirection.DOWN in directions:
        return TrendSignal.DOWN
    return TrendSignal.STABLE


def _assess_seasonality(snap: IntelligenceSnapshot) -> SeasonalitySignal:
    if not snap.seasonality:
        return SeasonalitySignal.UNKNOWN
    current_month = datetime.fromtimestamp(snap.generated_at).month
    idx = snap.seasonality.get(current_month)
    if idx is None:
        return SeasonalitySignal.UNKNOWN
    if idx >= _SEASONAL_PEAK_THRESHOLD:
        return SeasonalitySignal.PEAK
    if idx <= _SEASONAL_DROP_THRESHOLD:
        return SeasonalitySignal.DROP
    return SeasonalitySignal.NORMAL


def _assess_event(snap: IntelligenceSnapshot) -> EventSignal:
    """Наиболее значимый тип события (priority: REGULATION > ECONOMIC > SALE > HOLIDAY > COMPETITOR > PLATFORM)."""
    if not snap.market_events:
        return EventSignal.NONE
    priority = [
        EventType.REGULATION, EventType.ECONOMIC, EventType.SALE,
        EventType.HOLIDAY, EventType.COMPETITOR, EventType.PLATFORM,
    ]
    present = {e.event_type for e in snap.market_events}
    for et in priority:
        if et in present:
            return EventSignal[et.value.upper()]
    return EventSignal.NONE


# ─── правила ─────────────────────────────────────────────────────── #

def _ev_ids(snap: IntelligenceSnapshot, limit: int = 5) -> list[str]:
    return [e.id for e in snap.evidence[:limit]]


def _rule_high_demand_up_trend(
    snap: IntelligenceSnapshot,
    demand: DemandSignal,
    trend: TrendSignal,
) -> list[Conclusion]:
    if demand != DemandSignal.HIGH or trend != TrendSignal.UP:
        return []
    return [Conclusion(
        type=ConclusionType.OPPORTUNITY,
        claim="[INFERENCE] Высокий спрос совпадает с восходящим трендом.",
        rationale=(
            "Зафиксировано несколько сигналов спроса и тренд роста по категории. "
            "Возможно, стоит рассмотреть усиление рекламы или пополнение остатков."
        ),
        confidence=min(0.80, snap.confidence + 0.10),
        evidence_ids=_ev_ids(snap),
    )]


def _rule_high_demand_down_trend(
    snap: IntelligenceSnapshot,
    demand: DemandSignal,
    trend: TrendSignal,
) -> list[Conclusion]:
    if demand != DemandSignal.HIGH or trend != TrendSignal.DOWN:
        return []
    return [Conclusion(
        type=ConclusionType.RISK,
        claim="[INFERENCE] Высокий спрос сочетается с нисходящим трендом — возможная смена рынка.",
        rationale=(
            "Текущий спрос высокий, однако тренд показывает снижение. "
            "Это может быть предвестником падения продаж — рекомендуется мониторинг."
        ),
        confidence=min(0.75, snap.confidence + 0.05),
        evidence_ids=_ev_ids(snap),
    )]


def _rule_seasonal_peak(
    snap: IntelligenceSnapshot,
    demand: DemandSignal,
    seasonal: SeasonalitySignal,
) -> list[Conclusion]:
    if seasonal != SeasonalitySignal.PEAK:
        return []
    if demand not in (DemandSignal.HIGH, DemandSignal.MEDIUM):
        return []
    return [Conclusion(
        type=ConclusionType.OPPORTUNITY,
        claim="[INFERENCE] Сезонный пик совпадает с высоким спросом.",
        rationale=(
            "Текущий месяц исторически является пиком продаж в данной категории. "
            "При наличии остатков — хороший момент для активизации продаж."
        ),
        confidence=min(0.75, snap.confidence + 0.08),
        evidence_ids=_ev_ids(snap),
    )]


def _rule_seasonal_drop(
    snap: IntelligenceSnapshot,
    trend: TrendSignal,
    seasonal: SeasonalitySignal,
) -> list[Conclusion]:
    if seasonal != SeasonalitySignal.DROP:
        return []
    if trend not in (TrendSignal.DOWN, TrendSignal.STABLE, TrendSignal.UNKNOWN):
        return []
    return [Conclusion(
        type=ConclusionType.RISK,
        claim="[INFERENCE] Сезонный спад — возможно снижение продаж.",
        rationale=(
            "Текущий месяц исторически является периодом низкого спроса. "
            "Нисходящий или стабильный тренд усиливает этот сигнал."
        ),
        confidence=min(0.70, snap.confidence + 0.05),
        evidence_ids=_ev_ids(snap),
    )]


def _rule_sale(
    snap: IntelligenceSnapshot,
    demand: DemandSignal,
    event: EventSignal,
) -> list[Conclusion]:
    if event != EventSignal.SALE:
        return []
    conclusions: list[Conclusion] = []
    ev_ids = [e.id for e in snap.market_events
              if e.event_type == EventType.SALE][:3] + _ev_ids(snap, 2)
    ev_ids = list(dict.fromkeys(ev_ids))  # dedup preserving order

    if demand in (DemandSignal.HIGH, DemandSignal.MEDIUM):
        conclusions.append(Conclusion(
            type=ConclusionType.OPPORTUNITY,
            claim="[FACT] Активна рекламная акция при высоком спросе.",
            rationale=(
                "Зафиксирована распродажа/акция в категории. "
                "При высоком спросе это создаёт возможность для роста видимости. "
                "Примечание: повышение цены в период акции не рекомендуется."
            ),
            confidence=min(0.72, snap.confidence + 0.08),
            evidence_ids=ev_ids,
        ))
    else:
        conclusions.append(Conclusion(
            type=ConclusionType.MONITOR,
            claim="[FACT] Зафиксирована акция/распродажа в категории.",
            rationale="Спрос неизвестен или низкий — рекомендуется наблюдение без активных изменений.",
            confidence=snap.confidence,
            evidence_ids=ev_ids,
        ))
    return conclusions


def _rule_regulation(
    snap: IntelligenceSnapshot,
    event: EventSignal,
) -> list[Conclusion]:
    if event != EventSignal.REGULATION:
        return []
    ev_ids = [e.id for e in snap.market_events
              if e.event_type == EventType.REGULATION][:3] + _ev_ids(snap, 2)
    ev_ids = list(dict.fromkeys(ev_ids))
    return [Conclusion(
        type=ConclusionType.RISK,
        claim="[FACT] Зафиксировано регуляторное событие в категории.",
        rationale=(
            "Новые требования или изменения законодательства могут повлиять на продажи. "
            "Рекомендуется проверить соответствие карточки и документов."
        ),
        confidence=min(0.78, snap.confidence + 0.10),
        evidence_ids=ev_ids,
    )]


def _rule_competitor(
    snap: IntelligenceSnapshot,
    event: EventSignal,
) -> list[Conclusion]:
    if event != EventSignal.COMPETITOR:
        return []
    ev_ids = [e.id for e in snap.market_events
              if e.event_type == EventType.COMPETITOR][:3] + _ev_ids(snap, 2)
    ev_ids = list(dict.fromkeys(ev_ids))
    return [Conclusion(
        type=ConclusionType.MONITOR,
        claim="[FACT] Зафиксирована активность конкурента в категории.",
        rationale=(
            "Наблюдается конкурентное событие. Прямой вывод о влиянии на продажи "
            "невозможен без дополнительных данных. Рекомендуется мониторинг."
        ),
        confidence=snap.confidence * 0.90,
        evidence_ids=ev_ids,
    )]


def _rule_conflict(
    snap: IntelligenceSnapshot,
    trend: TrendSignal,
) -> list[Conclusion]:
    if trend != TrendSignal.CONFLICT:
        return []
    return [Conclusion(
        type=ConclusionType.RISK,
        claim="[OBSERVATION] Конфликтующие трендовые сигналы — высокая неопределённость.",
        rationale=(
            "Одновременно зафиксированы восходящий и нисходящий тренды. "
            "Принятие решений на основе одного из них может быть ошибочным. "
            "Рекомендуется дождаться более чётких сигналов."
        ),
        confidence=max(0.40, snap.confidence - 0.10),
        evidence_ids=_ev_ids(snap),
        is_conflict=True,
    )]


def _rule_economic(
    snap: IntelligenceSnapshot,
    event: EventSignal,
) -> list[Conclusion]:
    if event != EventSignal.ECONOMIC:
        return []
    ev_ids = [e.id for e in snap.market_events
              if e.event_type == EventType.ECONOMIC][:3] + _ev_ids(snap, 2)
    ev_ids = list(dict.fromkeys(ev_ids))
    return [Conclusion(
        type=ConclusionType.RISK,
        claim="[FACT] Зафиксировано макроэкономическое событие, способное влиять на спрос.",
        rationale=(
            "Экономическое событие (курс, ставка, инфляция) может изменить "
            "покупательскую способность. Конкретное влияние на категорию "
            "без дополнительных данных определить невозможно."
        ),
        confidence=min(0.65, snap.confidence + 0.05),
        evidence_ids=ev_ids,
    )]


# ─── review intelligence rules ────────────────────────────────────── #

_REVIEW_RISK_TYPES = frozenset({
    "QUALITY", "PRODUCT_QUALITY", "PACKAGING", "UNPACKING", "COMPLETENESS",
    "DAMAGE", "DELIVERY", "LOGISTICS", "FUNCTIONALITY", "PHOTO_MATCH",
    "DESCRIPTION_MATCH", "EXPECTATIONS",
})
_MIN_REVIEW_COUNT = 2
_MIN_REVIEW_RATIO = 0.10
_MIN_REVIEW_CONF = 0.40


def _rule_review_issues(snap: IntelligenceSnapshot) -> list[Conclusion]:
    """
    Recurring review issues → RISK / STRENGTH / MONITOR.

    Один слабый отзыв НЕ создаёт RISK.
    """
    issues = list(getattr(snap, "review_issues", None) or [])
    if not issues:
        return []

    recurring = [
        i for i in issues
        if getattr(i, "count", 0) >= _MIN_REVIEW_COUNT
        and getattr(i, "ratio", 0) >= _MIN_REVIEW_RATIO
        and getattr(i, "confidence", 0) >= _MIN_REVIEW_CONF
    ]

    out: list[Conclusion] = []

    if not recurring:
        weak = [i for i in issues if getattr(i, "count", 0) >= 1]
        if len(weak) >= 3:
            ids = []
            for i in weak[:5]:
                ids.extend(getattr(i, "source_ids", None) or [i.id])
            out.append(Conclusion(
                type=ConclusionType.MONITOR,
                claim="[OBSERVATION] Много разных слабых сигналов в отзывах — паттерн не подтверждён.",
                rationale="Отдельные отзывы есть, но recurring issue не достиг порога.",
                confidence=min(0.45, snap.confidence + 0.05),
                evidence_ids=list(dict.fromkeys(ids))[:8],
            ))
        return out

    for issue in recurring:
        stype = getattr(issue.signal_type, "value", str(issue.signal_type))
        sentiment = getattr(issue.sentiment, "value", str(issue.sentiment))
        ids = list(getattr(issue, "source_ids", None) or [issue.id])
        claim_text = (getattr(issue, "claim", "") or "")[:140]

        if sentiment == "NEGATIVE" and stype in _REVIEW_RISK_TYPES:
            out.append(Conclusion(
                type=ConclusionType.RISK,
                claim=f"[OBSERVATION] Повторяющаяся проблема в отзывах ({stype}): {claim_text}",
                rationale=(
                    f"Группа из {issue.count} отзывов "
                    f"(доля {issue.ratio:.0%}, confidence {issue.confidence:.0%})."
                ),
                confidence=min(0.85, issue.confidence),
                evidence_ids=ids[:8],
            ))
        elif sentiment == "POSITIVE" and stype != "OTHER":
            out.append(Conclusion(
                type=ConclusionType.STRENGTH,
                claim=f"[OBSERVATION] Повторяющаяся сильная сторона ({stype}): {claim_text}",
                rationale=(
                    f"Группа из {issue.count} положительных отзывов "
                    f"(доля {issue.ratio:.0%})."
                ),
                confidence=min(0.80, issue.confidence),
                evidence_ids=ids[:8],
            ))

    return out


# ─── итоговый confidence ──────────────────────────────────────────── #

def _overall_confidence(snap: IntelligenceSnapshot, conclusions: list[Conclusion]) -> float:
    """
    Сводный confidence оценки.

    Базис — snapshot.confidence.
    Корректировка: среднее confidence выводов (если есть).
    """
    if not conclusions:
        return round(max(0.0, snap.confidence - 0.05), 4)
    avg_c = sum(c.confidence for c in conclusions) / len(conclusions)
    combined = (snap.confidence * 0.50) + (avg_c * 0.50)
    return round(min(0.90, max(0.0, combined)), 4)
