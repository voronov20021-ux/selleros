"""
funnel_economics.py — ARGUS Funnel + Unit Economics layer.

Детерминированный слой поверх SellerBrain + Advisor + Finance:
находит, где товар теряет деньги в воронке (Cases A–D),
считает CTR/CVR честно, связывает RI/card/finance,
не выдумывает метрики и не пишет шаблонные советы без evidence.

Не трогает Browser / WB Engine / SFP / commercial / RI extractor.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from typing import Any, Iterable

from backend.ai.advisor import compute_unit_economics
from backend.ai.finance_planner import (
    FinancialContext,
    ProductCandidate,
    KnowledgeLayer,
    calculate,
    candidates_from_session,
    resolve_product,
)


# --------------------------------------------------------------------------- #
# Thresholds (aligned with advisor funnel heuristics)
# --------------------------------------------------------------------------- #

CTR_OK = 2.0       # % — ниже = low
CVR_OK = 5.0       # % — ниже = low
MARGIN_WEAK = 12.0  # % — ниже при OK funnel → Case D
SMALL_ORDERS = 10   # малая выборка заказов → CHECK, не системный диагноз
SMALL_IMPRESSIONS = 500


# --------------------------------------------------------------------------- #
# Query markers
# --------------------------------------------------------------------------- #

_FUNNEL_MARKERS = (
    "воронк", "ctr", "цтр", "cvr", "конверси",
    "почему не продаж", "не продаёт", "не продает", "не продаются",
    "не продаются", "где теряю", "где теряем", "где деньги",
    "показы", "клики", "unit economics", "юнит эконом",
    "почему товар не", "почему кроссов", "не приносит денег",
    "полностью разбер", "разберись почему",
)

_FUNNEL_SHORT = (
    "какой ctr", "какой cvr", "какой цтр", "какой конверси",
    "сколько ctr", "сколько cvr", "покажи ctr", "покажи cvr",
)

_FUNNEL_DEEP = (
    "полностью", "разберись", "глубоко", "почему не приносит",
    "где теряю деньги", "полный разбор", "всё разбер",
)

_TEMPLATE_BANS = (
    "улучшите описание",
    "добавьте характеристики",
    "собирайте больше отзывов",
    "сделайте качественные фотографии",
    "нужно улучшить характеристики",
)


def _norm(text: str) -> str:
    return (text or "").lower().replace("ё", "е").strip()


def is_funnel_query(text: str) -> bool:
    """Явный запрос воронки / «почему не продаётся» / unit economics в funnel-смысле."""
    low = _norm(text)
    if not low:
        return False
    # finance procurement steals «unit economics» только при закупке — иначе funnel
    if any(m in low for m in ("закуп", "парти", "сколько выйдет", "заказать кг")):
        return False
    if any(m in low for m in _FUNNEL_MARKERS):
        return True
    if any(m in low for m in _FUNNEL_SHORT):
        return True
    # «почему … не …» + товарный контекст
    if "почему" in low and any(
        w in low for w in ("продаж", "заказ", "выручк", "прибыл", "денег", "конверси")
    ):
        return True
    return False


def is_funnel_followup(text: str, *, has_ctx: bool) -> bool:
    if not has_ctx:
        return False
    low = _norm(text)
    if not low:
        return False
    if is_funnel_query(text):
        return True
    if any(m in low for m in ("а если", "пересчитай", "а ctr", "а cvr", "тогда")):
        return True
    # чистое число как CTR/CVR
    if re.fullmatch(r"[\d\s.,]+%?", low):
        return True
    return False


def should_handle_funnel(text: str, *, has_ctx: bool = False) -> bool:
    return is_funnel_query(text) or is_funnel_followup(text, has_ctx=has_ctx)


def reply_depth(text: str) -> str:
    """short | deep | normal"""
    low = _norm(text)
    if any(m in low for m in _FUNNEL_SHORT) and not any(m in low for m in _FUNNEL_DEEP):
        return "short"
    if any(m in low for m in _FUNNEL_DEEP):
        return "deep"
    if is_funnel_query(text) and len(low) > 90:
        return "deep"
    return "normal"


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #

class FunnelCase(str, Enum):
    A_ENTRY = "A_ENTRY"                 # low CTR + OK CVR
    B_AFTER_CLICK = "B_AFTER_CLICK"     # OK CTR + low CVR
    C_TWO_STAGE = "C_TWO_STAGE"         # both low
    D_UNIT_ECON = "D_UNIT_ECON"         # OK funnel + weak profit
    HEALTHY = "HEALTHY"                 # OK funnel + OK econ / no action
    INSUFFICIENT = "INSUFFICIENT"       # missing CTR/CVR
    CHECK = "CHECK"                     # small sample


class MetricStatus(str, Enum):
    LOW = "low"
    OK = "ok"
    UNKNOWN = "unknown"


@dataclass
class FunnelMetrics:
    impressions: float | None = None
    clicks: float | None = None
    orders: float | None = None
    ctr: float | None = None
    cvr: float | None = None
    ctr_source: str = "unknown"   # seller | api | computed | unknown
    cvr_source: str = "unknown"
    ctr_status: MetricStatus = MetricStatus.UNKNOWN
    cvr_status: MetricStatus = MetricStatus.UNKNOWN
    known: list[str] = field(default_factory=list)
    assumed: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ctr_status"] = self.ctr_status.value
        d["cvr_status"] = self.cvr_status.value
        return d


@dataclass
class ReviewSignal:
    category: str
    label: str = ""
    recurring: bool = False
    evidence_n: int = 0


@dataclass
class CardSignal:
    n_photos: int | None = None
    first_photo_weak: bool = False
    desc_len: int | None = None
    chars_n: int | None = None
    price: float | None = None
    competitor_median: float | None = None
    price_above_market: bool = False
    score: float | None = None
    healthy: bool = False


@dataclass
class FunnelContext:
    """Память funnel-допущений / последних метрик в сессии."""

    article: int | None = None
    product_title: str | None = None
    impressions: float | None = None
    clicks: float | None = None
    orders: float | None = None
    ctr: float | None = None
    cvr: float | None = None
    last_case: str | None = None
    provenance: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "FunnelContext":
        if not data:
            return cls()
        allowed = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in allowed}
        ctx = cls(**kwargs)
        if not isinstance(ctx.provenance, dict):
            ctx.provenance = {}
        return ctx

    def set_field(self, key: str, value: float | int | None, *, source: str = "user") -> None:
        if value is None or not hasattr(self, key):
            return
        setattr(self, key, float(value))
        self.provenance[key] = source


@dataclass
class FunnelDiagnosis:
    case: FunnelCase
    title: str
    why: str
    confidence: float
    confidence_band: str  # высокая | средняя | низкая
    confidence_why: str
    do_first: list[str] = field(default_factory=list)
    leave_alone: list[str] = field(default_factory=list)
    check: list[str] = field(default_factory=list)
    figures: list[str] = field(default_factory=list)
    metrics: FunnelMetrics | None = None
    unit: dict[str, Any] | None = None
    locus: str = "UNKNOWN"
    action_class: str = "CHECK"  # NO_ACTION | CHECK | ACTION | FUNNEL_HYPOTHESIS | INSUFFICIENT_DATA
    ri_link: str | None = None
    card_link: str | None = None
    layers_known: list[str] = field(default_factory=list)
    layers_assumed: list[str] = field(default_factory=list)
    layers_unknown: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case": self.case.value,
            "title": self.title,
            "why": self.why,
            "confidence": self.confidence,
            "confidence_band": self.confidence_band,
            "confidence_why": self.confidence_why,
            "do_first": list(self.do_first),
            "leave_alone": list(self.leave_alone),
            "check": list(self.check),
            "figures": list(self.figures),
            "locus": self.locus,
            "action_class": self.action_class,
            "ri_link": self.ri_link,
            "card_link": self.card_link,
            "unit": self.unit,
            "metrics": self.metrics.to_dict() if self.metrics else None,
        }


@dataclass
class FunnelTurnResult:
    text: str
    diagnosis: FunnelDiagnosis
    ctx: FunnelContext
    resolved: ProductCandidate | None = None
    clarify: str | None = None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "не известен"
    return f"{v:.1f}%".replace(".0%", "%") if abs(v - round(v)) < 1e-9 else f"{v:.1f}%"


def _fmt_money(v: float | None) -> str:
    if v is None:
        return "н/д"
    if abs(v - round(v)) < 1e-9:
        return f"{int(round(v)):,}".replace(",", " ")
    return f"{v:,.2f}".replace(",", " ")


def _confidence_band(c: float) -> tuple[str, str]:
    if c >= 0.70:
        return "высокая", "сигналы согласованы и опираются на метрики"
    if c >= 0.45:
        return "средняя", "есть сигналы, но выборка или часть данных ограничены"
    return "низкая", "данных мало — вывод предварительный, нужна проверка"


def _status(value: float | None, ok_threshold: float) -> MetricStatus:
    if value is None:
        return MetricStatus.UNKNOWN
    return MetricStatus.OK if value >= ok_threshold else MetricStatus.LOW


# --------------------------------------------------------------------------- #
# Metric computation
# --------------------------------------------------------------------------- #

def extract_funnel_numbers(text: str) -> dict[str, float]:
    """Извлечь CTR/CVR/показы/клики/заказы из реплики продавца."""
    low = _norm(text)
    found: dict[str, float] = {}
    num = r"(\d+(?:[.,]\d+)?)"

    patterns: list[tuple[str, str]] = [
        (rf"ctr\s*[:=]?\s*{num}\s*%?", "ctr"),
        (rf"цтр\s*[:=]?\s*{num}\s*%?", "ctr"),
        (rf"cvr\s*[:=]?\s*{num}\s*%?", "cvr"),
        (rf"конверси\w*\s*[:=]?\s*{num}\s*%?", "cvr"),
        (rf"показы?\s*[:=]?\s*{num}", "impressions"),
        (rf"{num}\s*показ", "impressions"),
        (rf"клики?\s*[:=]?\s*{num}", "clicks"),
        (rf"{num}\s*клик", "clicks"),
        (rf"заказ(?:ов|а)?\s*[:=]?\s*{num}", "orders"),
        (rf"{num}\s*заказ", "orders"),
    ]
    for pat, key in patterns:
        m = re.search(pat, low)
        if not m or key in found:
            continue
        try:
            found[key] = float(m.group(1).replace(",", "."))
        except ValueError:
            continue
    return found


def compute_funnel_metrics(
    *,
    seller_data=None,
    impressions: float | None = None,
    clicks: float | None = None,
    orders: float | None = None,
    ctr: float | None = None,
    cvr: float | None = None,
    ctr_source: str | None = None,
    cvr_source: str | None = None,
) -> FunnelMetrics:
    """
    CTR/CVR: seller/API source-of-truth; иначе вычислить из входов;
    missing → UNKNOWN, никогда не «низкий» без данных.
    """
    m = FunnelMetrics()

    def _from_seller(name: str) -> float | None:
        if seller_data is None:
            return None
        return _safe_float(getattr(seller_data, name, None))

    def _src(name: str) -> str | None:
        if seller_data is None:
            return None
        return getattr(seller_data, f"{name}_source", None)

    m.impressions = impressions if impressions is not None else _from_seller("impressions")
    m.orders = orders if orders is not None else _from_seller("orders")
    # clicks: explicit → seller.views as card views proxy → None
    if clicks is not None:
        m.clicks = clicks
    else:
        m.clicks = _from_seller("clicks")
        if m.clicks is None:
            # views ≈ переходы в карточку (честно помечаем)
            views = _from_seller("views")
            if views is not None:
                m.clicks = views
                m.assumed.append("клики ≈ просмотры карточки (views)")

    # CTR SoT
    provided_ctr = ctr if ctr is not None else _from_seller("ctr")
    src_ctr = ctr_source or _src("ctr") or ("seller" if provided_ctr is not None else None)
    if provided_ctr is not None:
        m.ctr = provided_ctr
        m.ctr_source = src_ctr or "seller"
        m.known.append(f"CTR {_fmt_pct(m.ctr)} ({m.ctr_source})")
    elif m.impressions is not None and m.clicks is not None and m.impressions > 0:
        m.ctr = round(m.clicks / m.impressions * 100.0, 2)
        m.ctr_source = "computed"
        m.assumed.append(f"CTR {_fmt_pct(m.ctr)} = клики/показы")
    else:
        m.ctr = None
        m.ctr_source = "unknown"
        m.unknown.append("CTR")

    # CVR SoT
    provided_cvr = cvr if cvr is not None else _from_seller("cvr")
    src_cvr = cvr_source or _src("cvr") or ("seller" if provided_cvr is not None else None)
    if provided_cvr is not None:
        m.cvr = provided_cvr
        m.cvr_source = src_cvr or "seller"
        m.known.append(f"CVR {_fmt_pct(m.cvr)} ({m.cvr_source})")
    elif m.clicks is not None and m.orders is not None and m.clicks > 0:
        m.cvr = round(m.orders / m.clicks * 100.0, 2)
        m.cvr_source = "computed"
        m.assumed.append(f"CVR {_fmt_pct(m.cvr)} = заказы/клики")
    else:
        m.cvr = None
        m.cvr_source = "unknown"
        m.unknown.append("CVR")

    if m.impressions is not None:
        m.known.append(f"показы {int(m.impressions)}")
    else:
        m.unknown.append("показы")
    if m.clicks is not None and "клики ≈" not in " ".join(m.assumed):
        m.known.append(f"клики {int(m.clicks)}")
    elif m.clicks is None:
        m.unknown.append("клики")
    if m.orders is not None:
        m.known.append(f"заказы {int(m.orders)}")
    else:
        m.unknown.append("заказы")

    m.ctr_status = _status(m.ctr, CTR_OK)
    m.cvr_status = _status(m.cvr, CVR_OK)
    return m


def build_unit_economics(
    *,
    seller_data=None,
    product=None,
    finance_ctx: FinancialContext | None = None,
) -> dict[str, Any]:
    """
    Unit economics: reuse advisor.compute_unit_economics;
    optionally enrich with finance_planner batch calc (break-even etc.).
    Incomplete → don't invent.
    """
    unit = compute_unit_economics(seller_data, product)
    out = dict(unit)
    out["margin_vs_markup_note"] = (
        "Маржа % = прибыль/выручка; наценка % = прибыль/себестоимость — это разные метрики."
    )

    # Per-unit from finance context when seller private incomplete
    if finance_ctx is not None:
        calc = calculate(finance_ctx)
        out["finance"] = {
            "revenue": calc.revenue,
            "net_profit": calc.net_profit or calc.after_ads or calc.after_commission or calc.gross_profit,
            "margin_pct": calc.margin_pct,
            "markup_pct": calc.markup_pct,
            "min_sell_price": calc.min_sell_price,
            "max_ads": calc.max_ads,
            "max_purchase": calc.max_purchase,
            "units_to_breakeven": calc.units_to_breakeven,
            "missing": list(calc.missing),
            "not_included": list(calc.not_included),
        }
        if not out.get("complete") and calc.revenue is not None and calc.unit_cogs is not None:
            # still incomplete if commission/ads missing — honest
            missing = list(calc.not_included) + list(calc.missing)
            out["text"] = (
                "Продажи/партия считаются частично: "
                + (f"известная себестоимость/ед. {_fmt_money(calc.unit_cogs)} ₽. " if calc.unit_cogs else "")
                + ("Не хватает: " + ", ".join(missing[:4]) if missing else "")
            )
            out["partial_finance"] = True
        if calc.margin_pct is not None and calc.markup_pct is not None:
            out["margin_pct_finance"] = calc.margin_pct
            out["markup_pct_finance"] = calc.markup_pct

    # Explicit missing flags for tests
    if seller_data is not None:
        if getattr(seller_data, "commission", None) is None:
            out.setdefault("missing", [])
            if "комиссия" not in out["missing"]:
                out["missing"] = list(out.get("missing") or []) + (
                    [] if "комиссия" in (out.get("missing") or []) else ["комиссия"]
                )
        if getattr(seller_data, "ad_spend", None) is None:
            om = list(out.get("optional_missing") or [])
            if "реклама/ед." not in om:
                om.append("реклама/ед.")
            out["optional_missing"] = om
        if getattr(seller_data, "returns", None) is None:
            om = list(out.get("optional_missing") or [])
            if "возвраты" not in om:
                om.append("возвраты")
            out["optional_missing"] = om

    return out


# --------------------------------------------------------------------------- #
# Card / RI signals
# --------------------------------------------------------------------------- #

def card_signals_from_product(product=None, *, market_compare: dict | None = None) -> CardSignal:
    sig = CardSignal()
    if product is None:
        return sig
    photos = getattr(product, "photos", None) or []
    n = len(photos) if not isinstance(photos, int) else int(photos)
    sig.n_photos = n
    sig.first_photo_weak = n < 5
    desc = getattr(product, "description", None) or ""
    sig.desc_len = len(desc)
    chars = getattr(product, "characteristics", None) or {}
    sig.chars_n = len(chars) if isinstance(chars, dict) else 0
    sig.price = _safe_float(getattr(product, "price", None))
    if market_compare and market_compare.get("median") is not None:
        sig.competitor_median = _safe_float(market_compare.get("median"))
        if sig.price is not None and sig.competitor_median is not None:
            sig.price_above_market = sig.price > sig.competitor_median * 1.08
    rating = _safe_float(getattr(product, "rating", None))
    fb = getattr(product, "feedbacks", None)
    sig.healthy = (
        n >= 8
        and len(desc) >= 300
        and (sig.chars_n or 0) >= 5
        and (rating is None or rating >= 4.5)
        and (fb is None or int(fb) >= 10)
    )
    return sig


def review_signals_from_risks(risks: Iterable[Any] | None) -> list[ReviewSignal]:
    out: list[ReviewSignal] = []
    for r in risks or []:
        if isinstance(r, ReviewSignal):
            out.append(r)
            continue
        if isinstance(r, dict):
            cat = str(r.get("category") or r.get("type") or "")
            out.append(ReviewSignal(
                category=cat.upper(),
                label=str(r.get("label") or r.get("text") or cat),
                recurring=bool(r.get("recurring") or r.get("systemic")),
                evidence_n=int(r.get("evidence_n") or r.get("n") or 0),
            ))
            continue
        cat = str(getattr(r, "category", None) or getattr(r, "type", "") or "")
        meta = getattr(r, "metadata", None) or {}
        out.append(ReviewSignal(
            category=cat.upper(),
            label=str(getattr(r, "text", None) or cat),
            recurring=bool(meta.get("recurring") or meta.get("systemic")),
            evidence_n=int(meta.get("evidence_n") or meta.get("n") or 0),
        ))
    return out


# --------------------------------------------------------------------------- #
# Diagnosis Cases A–D
# --------------------------------------------------------------------------- #

def diagnose_funnel(
    metrics: FunnelMetrics,
    *,
    unit: dict[str, Any] | None = None,
    card: CardSignal | None = None,
    reviews: list[ReviewSignal] | None = None,
    product=None,
) -> FunnelDiagnosis:
    """Главная логика locus: Cases A–D + healthy / insufficient / small sample."""
    card = card or CardSignal()
    reviews = reviews or []
    unit = unit or {}

    figures: list[str] = []
    if metrics.ctr is not None:
        figures.append(f"CTR: {_fmt_pct(metrics.ctr)}")
    else:
        figures.append("CTR: не известен")
    if metrics.cvr is not None:
        figures.append(f"CVR: {_fmt_pct(metrics.cvr)}")
    else:
        figures.append("CVR: не известен")
    price = card.price or (unit.get("price") if unit else None)
    if price is not None:
        figures.append(f"Цена: {_fmt_money(float(price))} ₽")
    if unit.get("complete") and unit.get("contribution") is not None:
        figures.append(f"Прибыль/шт: {_fmt_money(unit['contribution'])} ₽")
        if unit.get("margin_pct") is not None:
            figures.append(f"Маржинальность: {unit['margin_pct']}%")

    layers_known = list(metrics.known)
    layers_assumed = list(metrics.assumed)
    layers_unknown = list(metrics.unknown)

    # Small sample → CHECK
    small = False
    if metrics.orders is not None and metrics.orders < SMALL_ORDERS:
        small = True
    if metrics.impressions is not None and metrics.impressions < SMALL_IMPRESSIONS and (
        metrics.ctr is not None or metrics.cvr is not None
    ):
        small = True

    ctr_s, cvr_s = metrics.ctr_status, metrics.cvr_status

    # Missing both → insufficient
    if ctr_s is MetricStatus.UNKNOWN and cvr_s is MetricStatus.UNKNOWN:
        band, why_b = _confidence_band(0.35)
        return FunnelDiagnosis(
            case=FunnelCase.INSUFFICIENT,
            title="Недостаточно данных по воронке",
            why=(
                "CTR пока не известен — данных по показам и кликам нет. "
                "CVR пока не известен — нет кликов/заказов или готовой метрики."
            ),
            confidence=0.35,
            confidence_band=band,
            confidence_why=why_b,
            do_first=[
                "Снять CTR/CVR/показы/заказы за период — иначе locus воронки не определить",
            ],
            leave_alone=[
                "Не утверждать «CTR низкий» или «CVR хороший» без цифр",
                "Не лить рекламу и не переписывать карточку вслепую",
            ],
            check=["CTR", "CVR", "показы", "клики", "заказы"],
            figures=figures,
            metrics=metrics,
            unit=unit,
            locus="UNKNOWN",
            action_class="INSUFFICIENT_DATA",
            layers_known=layers_known,
            layers_assumed=layers_assumed,
            layers_unknown=layers_unknown,
        )

    # Case A: low CTR + OK CVR (or CVR unknown but CTR low — entry hypothesis)
    if ctr_s is MetricStatus.LOW and cvr_s in (MetricStatus.OK, MetricStatus.UNKNOWN):
        conf = 0.72 if cvr_s is MetricStatus.OK else 0.55
        if small:
            conf = min(conf, 0.42)
        band, why_b = _confidence_band(conf)
        do: list[str] = []
        card_link = None
        if card.first_photo_weak or (card.n_photos is not None and card.n_photos < 5):
            do.append("Перетестировать первое фото — основной визуальный вход в выдаче")
            card_link = "слабое/мало фото на входе"
        else:
            do.append("Перетестировать первое фото (превью в выдаче)")
        if card.price_above_market:
            do.append(
                f"Сверить цену в выдаче с конкурентами "
                f"(сейчас {_fmt_money(card.price)} ₽ vs медиана {_fmt_money(card.competitor_median)} ₽)"
            )
            card_link = (card_link or "") + "; цена выше рынка"
        else:
            do.append("Проверить цену в выдаче относительно конкурентов")
        do.append("Через 7 дней сравнить CTR")
        leave = [
            "Описание и характеристики пока не приоритет",
            "Не чинить конверсию после клика, пока CTR слабый",
        ]
        if cvr_s is MetricStatus.UNKNOWN:
            leave.append("Конверсию (CVR) не утверждать — метрики нет")
        case = FunnelCase.CHECK if small else FunnelCase.A_ENTRY
        title = (
            "Проблема во входе в воронку, а не в покупке"
            if cvr_s is MetricStatus.OK
            else "Гипотеза: проблема во входе в воронку (CVR неизвестен)"
        )
        why = (
            f"CTR={_fmt_pct(metrics.ctr)} слабый"
            + (
                f", CVR={_fmt_pct(metrics.cvr)} нормальный — "
                "те, кто открывает карточку, покупают нормально."
                if cvr_s is MetricStatus.OK
                else ". CVR пока не известен — конверсию после клика не утверждаю."
            )
        )
        if small:
            why += " Выборка мала — это CHECK, не системный диагноз."
        return FunnelDiagnosis(
            case=case if not small else FunnelCase.CHECK,
            title=title,
            why=why,
            confidence=conf,
            confidence_band=band,
            confidence_why=why_b if not small else "малая выборка — не системный диагноз",
            do_first=do,
            leave_alone=leave,
            check=["CTR через 7 дней", "цена vs конкуренты"] + (
                ["CVR"] if cvr_s is MetricStatus.UNKNOWN else []
            ),
            figures=figures,
            metrics=metrics,
            unit=unit,
            locus="TRAFFIC",
            action_class="CHECK" if small else "FUNNEL_HYPOTHESIS",
            card_link=card_link,
            layers_known=layers_known,
            layers_assumed=layers_assumed,
            layers_unknown=layers_unknown,
        )

    # Case B: OK CTR + low CVR
    if cvr_s is MetricStatus.LOW and ctr_s in (MetricStatus.OK, MetricStatus.UNKNOWN):
        conf = 0.72 if ctr_s is MetricStatus.OK else 0.58
        if small:
            conf = min(conf, 0.42)
        band, why_b = _confidence_band(conf)
        ri = next(
            (
                r for r in reviews
                if any(k in r.category for k in (
                    "PHOTO_MATCH", "SIZE", "QUALITY", "PACKAGING", "DEFECT",
                )) and (r.recurring or r.evidence_n >= 3)
            ),
            None,
        )
        do = []
        ri_link = None
        if ri:
            label = ri.label or ri.category
            do.append(
                f"Сначала закрыть сигнал из отзывов ({ri.category}): {label[:80]}"
            )
            if "PHOTO_MATCH" in ri.category:
                do.append(
                    "Привести первое фото и фактический товар к одному виду — "
                    "не снижать цену первым шагом"
                )
                ri_link = (
                    "RI: recurring PHOTO_MATCH — люди кликают, но товар не совпадает "
                    "с ожиданием по фото"
                )
            else:
                ri_link = f"RI: {ri.category} связан с провалом после клика"
            do.append("После фикса повторно проверить CVR")
        else:
            do.append(
                "Разобрать послекликовую зону: цена в карточке, доверие, соответствие оффера"
            )
            do.append("Проверить отзывы на recurring-сигналы (фото/размер/качество)")
            do.append("Повторно снять CVR после правок")
        leave = [
            "Не наращивать трафик вслепую при слабом CVR",
        ]
        if ri and "PHOTO_MATCH" in ri.category:
            leave.append(
                "Не снижать цену первым действием — данных недостаточно считать цену причиной"
            )
        elif not ri:
            leave.append("Не утверждать причину цены без сравнения и отзывов")
        if ctr_s is MetricStatus.UNKNOWN:
            leave.append("Проблему клика/CTR не утверждать — CTR неизвестен")
        why = (
            (
                f"CTR={_fmt_pct(metrics.ctr)} нормальный/высокий, "
                if ctr_s is MetricStatus.OK
                else "CTR не известен, "
            )
            + f"CVR={_fmt_pct(metrics.cvr)} низкий → люди открывают карточку, но редко покупают."
        )
        if ri_link:
            why += f" {ri_link}."
        if small:
            why += " Выборка мала — CHECK, не системный диагноз."
        return FunnelDiagnosis(
            case=FunnelCase.CHECK if small else FunnelCase.B_AFTER_CLICK,
            title="Проблема после перехода в карточку",
            why=why,
            confidence=conf,
            confidence_band=band,
            confidence_why=why_b if not small else "малая выборка — не системный диагноз",
            do_first=do,
            leave_alone=leave,
            check=["CVR после фикса", "отзывы/RI"] + (
                ["CTR"] if ctr_s is MetricStatus.UNKNOWN else []
            ),
            figures=figures,
            metrics=metrics,
            unit=unit,
            locus="CONVERSION",
            action_class="CHECK" if small else ("ACTION" if ri else "FUNNEL_HYPOTHESIS"),
            ri_link=ri_link,
            layers_known=layers_known,
            layers_assumed=layers_assumed,
            layers_unknown=layers_unknown,
        )

    # Case C: both low
    if ctr_s is MetricStatus.LOW and cvr_s is MetricStatus.LOW:
        conf = 0.68
        if small:
            conf = 0.40
        band, why_b = _confidence_band(conf)
        return FunnelDiagnosis(
            case=FunnelCase.CHECK if small else FunnelCase.C_TWO_STAGE,
            title="Проблема в двух этапах воронки",
            why=(
                f"1) CTR={_fmt_pct(metrics.ctr)} слабый → вход в карточку. "
                f"2) CVR={_fmt_pct(metrics.cvr)} слабый → конверсия после перехода. "
                "Сначала вход (CTR), потом повторно оценить CVR."
                + (" Выборка мала — CHECK." if small else "")
            ),
            confidence=conf,
            confidence_band=band,
            confidence_why=why_b if not small else "малая выборка",
            do_first=[
                "Сначала исправить CTR / вход: превью и цена в выдаче",
                "Не чинить оба звена одновременно вслепую",
                "После роста CTR — повторно оценить CVR",
            ],
            leave_alone=[
                "Не выбирать случайно только одно звено как «единственную» проблему",
                "Не лить трафик до починки входа",
            ],
            check=["CTR", "затем CVR"],
            figures=figures,
            metrics=metrics,
            unit=unit,
            locus="TRAFFIC",
            action_class="CHECK" if small else "FUNNEL_HYPOTHESIS",
            layers_known=layers_known,
            layers_assumed=layers_assumed,
            layers_unknown=layers_unknown,
        )

    # Both OK (or one OK and other unknown treated carefully)
    both_ok = ctr_s is MetricStatus.OK and cvr_s is MetricStatus.OK
    profit_weak = False
    profit_known = False
    if unit.get("complete") and unit.get("margin_pct") is not None:
        profit_known = True
        profit_weak = float(unit["margin_pct"]) < MARGIN_WEAK
    elif unit.get("margin_pct_finance") is not None:
        profit_known = True
        profit_weak = float(unit["margin_pct_finance"]) < MARGIN_WEAK
    elif unit.get("complete") and unit.get("contribution") is not None:
        profit_known = True
        price_u = unit.get("price") or 0
        profit_weak = price_u > 0 and (float(unit["contribution"]) / float(price_u) * 100) < MARGIN_WEAK

    # Case D: OK funnel + weak unit economics
    if both_ok and profit_known and profit_weak:
        conf = 0.75
        band, why_b = _confidence_band(conf)
        do = [
            "Пересчитать закупочную цену (COGS) и рекламный бюджет",
            "Проверить комиссию, логистику и возвраты — не карточку",
        ]
        fin = (unit.get("finance") or {}) if isinstance(unit.get("finance"), dict) else {}
        if fin.get("max_ads") is not None:
            do.append(f"Максимум на рекламу партии ≈ {_fmt_money(fin['max_ads'])} ₽ (по известным данным)")
        if fin.get("max_purchase") is not None:
            do.append(f"Макс. закупочная цена ≈ {_fmt_money(fin['max_purchase'])} ₽/шт")
        return FunnelDiagnosis(
            case=FunnelCase.D_UNIT_ECON,
            title="Воронка работает — проблема в unit economics",
            why=(
                f"CTR={_fmt_pct(metrics.ctr)}, CVR={_fmt_pct(metrics.cvr)} — товар продаётся нормально. "
                "Проблема не в карточке — экономика единицы/партии слишком слабая."
            ),
            confidence=conf,
            confidence_band=band,
            confidence_why=why_b,
            do_first=do,
            leave_alone=[
                "Карточку пока не переделывать — funnel уже нормальный",
                "Не лить больше рекламы без пересчёта маржи",
            ],
            check=["COGS", "комиссия", "реклама/ед.", "логистика", "возвраты"],
            figures=figures,
            metrics=metrics,
            unit=unit,
            locus="PRICE",
            action_class="ACTION",
            layers_known=layers_known + ([unit.get("text")] if unit.get("text") else []),
            layers_assumed=layers_assumed,
            layers_unknown=layers_unknown + list(unit.get("missing") or [])
            + list(unit.get("optional_missing") or []),
        )

    # Healthy / NO_ACTION
    if both_ok and (not profit_known or not profit_weak):
        conf = 0.70 if (card.healthy or not reviews) else 0.60
        band, why_b = _confidence_band(conf)
        econ_note = ""
        if unit.get("complete"):
            econ_note = f" Экономика: {unit.get('text')}."
        elif profit_known:
            econ_note = " Маржа по известным данным не выглядит провальной."
        else:
            layers_unknown = layers_unknown + ["полная unit economics"]
        return FunnelDiagnosis(
            case=FunnelCase.HEALTHY,
            title="Системной проблемы по воронке не видно",
            why=(
                f"CTR={_fmt_pct(metrics.ctr)}, CVR={_fmt_pct(metrics.cvr)} — воронка выглядит здоровой."
                + econ_note
            ),
            confidence=conf,
            confidence_band=band,
            confidence_why=why_b,
            do_first=[
                "Ничего критичного не менять — мониторить рейтинг/отзывы и экономику",
            ],
            leave_alone=[
                "Бессмысленные правки карточки при высоких CTR и CVR",
                "Повышение ставки вслепую",
            ],
            check=["остатки", "маржа при появлении cost+fees"],
            figures=figures,
            metrics=metrics,
            unit=unit,
            locus="UNKNOWN",
            action_class="NO_ACTION",
            layers_known=layers_known,
            layers_assumed=layers_assumed,
            layers_unknown=layers_unknown,
        )

    # One metric OK, other unknown — insufficient for hard case
    conf = 0.45
    band, why_b = _confidence_band(conf)
    missing = []
    if ctr_s is MetricStatus.UNKNOWN:
        missing.append("CTR пока не известен — данных по показам и кликам нет")
    if cvr_s is MetricStatus.UNKNOWN:
        missing.append("CVR пока не известен")
    return FunnelDiagnosis(
        case=FunnelCase.INSUFFICIENT,
        title="Частичные данные воронки — жёсткий locus не ставлю",
        why=". ".join(missing) + ".",
        confidence=conf,
        confidence_band=band,
        confidence_why=why_b,
        do_first=["Дособрать недостающие CTR/CVR за период"],
        leave_alone=["Не писать «низкий/хороший» про неизвестную метрику"],
        check=list(metrics.unknown),
        figures=figures,
        metrics=metrics,
        unit=unit,
        locus="UNKNOWN",
        action_class="INSUFFICIENT_DATA",
        layers_known=layers_known,
        layers_assumed=layers_assumed,
        layers_unknown=layers_unknown,
    )


# --------------------------------------------------------------------------- #
# Formatting (Human UX first screen)
# --------------------------------------------------------------------------- #

def _strip_templates(text: str) -> str:
    low = _norm(text)
    for ban in _TEMPLATE_BANS:
        if ban in low:
            return ""
    return text


def format_funnel_reply(
    diagnosis: FunnelDiagnosis,
    *,
    depth: str = "normal",
    product_title: str | None = None,
) -> str:
    """Human UX first screen without evidence=/freq=/IDs."""
    d = diagnosis

    if depth == "short":
        bits = []
        if d.metrics and d.metrics.ctr is not None:
            bits.append(f"CTR: {_fmt_pct(d.metrics.ctr)}")
        elif d.metrics and d.metrics.ctr is None:
            bits.append("CTR пока не известен — данных по показам и кликам нет.")
        if d.metrics and d.metrics.cvr is not None:
            bits.append(f"CVR: {_fmt_pct(d.metrics.cvr)}")
        elif d.metrics and d.metrics.cvr is None and "CTR пока" not in " ".join(bits):
            bits.append("CVR пока не известен.")
        if not bits:
            bits.append(d.title)
        return "\n".join(bits)

    lines: list[str] = []
    lines.append("🎯 ГЛАВНАЯ ПРОБЛЕМА")
    lines.append("")
    if product_title:
        lines.append(f"Товар: {product_title}")
    lines.append(d.title)
    lines.append(d.why)
    lines.append("")
    lines.append(f"Уверенность: {d.confidence_band} ({d.confidence:.0%}) — {d.confidence_why}")
    lines.append("")

    lines.append("📊 ЦИФРЫ")
    lines.append("")
    for f in d.figures:
        lines.append(f)
    if d.unit and d.unit.get("complete") and d.unit.get("text"):
        if not any("Прибыль" in x for x in d.figures):
            lines.append(d.unit["text"])
    elif d.unit and not d.unit.get("complete") and d.unit.get("text"):
        lines.append(d.unit["text"])
    lines.append("")

    lines.append("🔧 ЧТО ДЕЛАТЬ")
    lines.append("")
    for i, a in enumerate(d.do_first[:4], 1):
        clean = _strip_templates(a)
        if clean:
            lines.append(f"{i}. {clean}")
    lines.append("")

    lines.append("🚫 ЧТО НЕ ТРОГАТЬ")
    lines.append("")
    for a in d.leave_alone[:4]:
        clean = _strip_templates(a)
        if clean:
            lines.append(f"• {clean}")
    lines.append("")

    lines.append("❓ ЧТО ПРОВЕРИТЬ")
    lines.append("")
    for a in d.check[:5]:
        lines.append(f"• {a}")

    if depth == "deep":
        lines.append("")
        lines.append(f"{KnowledgeLayer.KNOWN.value}")
        for x in d.layers_known[:8]:
            lines.append(f"• {x}")
        if d.ri_link:
            lines.append(f"• {d.ri_link}")
        if d.card_link:
            lines.append(f"• карточка: {d.card_link}")
        lines.append("")
        lines.append(f"{KnowledgeLayer.ASSUMED.value}")
        if d.layers_assumed:
            for x in d.layers_assumed[:6]:
                lines.append(f"• {x}")
        else:
            lines.append("• —")
        lines.append("")
        lines.append(f"{KnowledgeLayer.UNKNOWN.value}")
        if d.layers_unknown:
            for x in d.layers_unknown[:8]:
                lines.append(f"• {x}")
        else:
            lines.append("• —")
        # finance break-even block if present
        fin = (d.unit or {}).get("finance") if d.unit else None
        if isinstance(fin, dict) and any(
            fin.get(k) is not None
            for k in ("min_sell_price", "max_ads", "max_purchase", "units_to_breakeven")
        ):
            lines.append("")
            lines.append("📐 Finance / break-even (по известным данным)")
            if fin.get("min_sell_price") is not None:
                lines.append(f"• мин. цена ≈ {_fmt_money(fin['min_sell_price'])} ₽")
            if fin.get("max_ads") is not None:
                lines.append(f"• макс. реклама партии ≈ {_fmt_money(fin['max_ads'])} ₽")
            if fin.get("max_purchase") is not None:
                lines.append(f"• макс. закупка ≈ {_fmt_money(fin['max_purchase'])} ₽/шт")
            if fin.get("units_to_breakeven") is not None:
                lines.append(f"• единиц до окупаемости ≈ {_fmt_money(fin['units_to_breakeven'])}")
            if fin.get("margin_pct") is not None and fin.get("markup_pct") is not None:
                lines.append(
                    f"• маржа {fin['margin_pct']:.1f}% (прибыль/выручка) ≠ "
                    f"наценка {fin['markup_pct']:.1f}% (прибыль/себестоимость)"
                )

    # Guard: never emit banned templates
    text = "\n".join(lines)
    for ban in _TEMPLATE_BANS:
        if ban in _norm(text) and d.case in (
            FunnelCase.A_ENTRY, FunnelCase.D_UNIT_ECON, FunnelCase.HEALTHY,
        ):
            # strip line containing ban
            text = "\n".join(
                ln for ln in text.splitlines()
                if ban not in _norm(ln)
            )
    # no internal dumps
    text = re.sub(r"evidence=[A-Za-z0-9_-]+", "", text)
    text = re.sub(r"frequency=\S+", "", text)
    text = re.sub(r"\bIDs?=\S+", "", text)
    return text.strip()


# --------------------------------------------------------------------------- #
# Turn handler
# --------------------------------------------------------------------------- #

def handle_funnel_turn(
    text: str,
    *,
    ctx: FunnelContext | None = None,
    seller_data=None,
    product=None,
    finance_ctx: FinancialContext | None = None,
    candidates: Iterable[ProductCandidate] | None = None,
    current: ProductCandidate | None = None,
    review_risks: Iterable[Any] | None = None,
    market_compare: dict | None = None,
    depth: str | None = None,
) -> FunnelTurnResult:
    """
    Один ход: resolve product → metrics → unit econ → diagnose → format.
    """
    ctx = ctx or FunnelContext()
    depth = depth or reply_depth(text)

    # Resolve product
    cands = list(candidates or [])
    resolved, ambiguous, clarify = resolve_product(
        text, cands, current=current,
    )
    if clarify and ambiguous:
        diag = FunnelDiagnosis(
            case=FunnelCase.INSUFFICIENT,
            title="Уточните товар",
            why=clarify,
            confidence=0.3,
            confidence_band="низкая",
            confidence_why="несколько похожих товаров",
            do_first=["Выберите артикул из списка"],
            leave_alone=[],
            check=[],
            action_class="CHECK",
        )
        return FunnelTurnResult(text=clarify, diagnosis=diag, ctx=ctx, clarify=clarify)

    if resolved is None and current is not None and is_funnel_query(text):
        resolved = current

    if resolved is not None:
        ctx.article = int(resolved.article)
        ctx.product_title = resolved.title or ctx.product_title

    # Merge numbers from text into ctx
    extracted = extract_funnel_numbers(text)
    for k, v in extracted.items():
        ctx.set_field(k, v, source="user")

    metrics = compute_funnel_metrics(
        seller_data=seller_data,
        impressions=ctx.impressions,
        clicks=ctx.clicks,
        orders=ctx.orders,
        ctr=ctx.ctr,
        cvr=ctx.cvr,
    )
    # persist resolved metrics into ctx
    if metrics.ctr is not None:
        ctx.ctr = metrics.ctr
        ctx.provenance.setdefault("ctr", metrics.ctr_source)
    if metrics.cvr is not None:
        ctx.cvr = metrics.cvr
        ctx.provenance.setdefault("cvr", metrics.cvr_source)
    if metrics.impressions is not None:
        ctx.impressions = metrics.impressions
    if metrics.clicks is not None:
        ctx.clicks = metrics.clicks
    if metrics.orders is not None:
        ctx.orders = metrics.orders

    unit = build_unit_economics(
        seller_data=seller_data,
        product=product,
        finance_ctx=finance_ctx,
    )
    card = card_signals_from_product(product, market_compare=market_compare)
    if card.price is None and resolved and resolved.price is not None:
        card.price = float(resolved.price)
    reviews = review_signals_from_risks(review_risks)

    diagnosis = diagnose_funnel(
        metrics, unit=unit, card=card, reviews=reviews, product=product,
    )
    ctx.last_case = diagnosis.case.value

    title = ctx.product_title or (
        str(getattr(product, "title", "") or "") if product is not None else None
    )
    reply = format_funnel_reply(diagnosis, depth=depth, product_title=title or None)
    return FunnelTurnResult(
        text=reply,
        diagnosis=diagnosis,
        ctx=ctx,
        resolved=resolved,
    )
