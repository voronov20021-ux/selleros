"""
dynamic_analytics.py — ARGUS Dynamic Analytics layer.

From point-in-time → what was → what is → delta → systemic? → where → what to do.

Reuses SellerBrain / Advisor / Funnel / Finance / Evidence patterns.
Does NOT invent history. SYNTHETIC series only in tests.
Does NOT touch Browser / WB Engine / SFP / commercial / Finance core
(call finance planner / funnel diagnose only).
"""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from typing import Any, Iterable, Sequence

from backend.ai.funnel_economics import (
    FunnelCase,
    FunnelMetrics,
    MetricStatus,
    diagnose_funnel,
    compute_funnel_metrics,
)
from backend.ai.advisor import compute_unit_economics


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

PERIODS_SEC: dict[str, float] = {
    "1d": 1 * 86400.0,
    "3d": 3 * 86400.0,
    "7d": 7 * 86400.0,
    "14d": 14 * 86400.0,
    "30d": 30 * 86400.0,
}

#: Rate metrics where absolute delta is percentage points (p.p.), not relative %.
RATE_METRICS = frozenset({"ctr", "cvr", "margin", "rating"})

#: Min points before we call a trend systemic (not 2 noisy points).
MIN_POINTS_SYSTEMIC = 3
MIN_POINTS_TREND = 2
MIN_POINTS_FORECAST = 4

CTR_DROP_PP = 0.3       # p.p. material CTR change
CVR_DROP_PP = 0.5
REL_SALES_DROP = 0.15   # 15% relative
STABLE_REL = 0.05       # ±5% → STABLE for volume metrics
STABLE_PP = 0.15        # ±0.15 p.p. for rates


# --------------------------------------------------------------------------- #
# Query markers
# --------------------------------------------------------------------------- #

_DYN_MARKERS = (
    "динамик", "тренд", "прогноз", "сравн с прошл", "сравни с прошл",
    "как изменил", "что было", "что стало", "за неделю", "за месяц",
    "за 7 дн", "за 14 дн", "за 30 дн", "за 3 дн",
    "упал ctr", "упала конверси", "растёт продаж", "растет продаж",
    "падение продаж", "рост продаж", "истори", "срез метрик",
    "почему ctr", "почему цтр", "почему конверси", "почему показы",
    "почему клик",
)

_DYN_SHORT = (
    "динамика", "тренд", "прогноз", "как дела по цифрам",
)

_DYN_DEEP = (
    "полностью", "разберись", "глубоко", "системн", "полный разбор",
)


def _norm(text: str) -> str:
    return (text or "").lower().replace("ё", "е").strip()


def is_dynamics_query(text: str) -> bool:
    low = _norm(text)
    if not low:
        return False
    # finance procurement wins
    if any(m in low for m in ("закуп", "парти", "сколько выйдет", "заказать кг")):
        return False
    if any(m in low for m in _DYN_MARKERS):
        return True
    if any(m in low for m in _DYN_SHORT):
        return True
    if "почему" in low and any(w in low for w in ("ctr", "цтр", "конверси", "показ", "клик")):
        return True
    return False


def is_dynamics_followup(text: str, *, has_ctx: bool) -> bool:
    if not has_ctx:
        return False
    low = _norm(text)
    if not low:
        return False
    if is_dynamics_query(text):
        return True
    if any(m in low for m in ("а если", "а за", "а тренд", "а прогноз", "пересчитай")):
        return True
    return False


def should_handle_dynamics(text: str, *, has_ctx: bool = False) -> bool:
    return is_dynamics_query(text) or is_dynamics_followup(text, has_ctx=has_ctx)


def reply_depth(text: str) -> str:
    low = _norm(text)
    if any(m in low for m in _DYN_SHORT) and not any(m in low for m in _DYN_DEEP):
        if len(low) < 40:
            return "short"
    if any(m in low for m in _DYN_DEEP):
        return "deep"
    if is_dynamics_query(text) and len(low) > 90:
        return "deep"
    return "normal"


def parse_period_hint(text: str) -> str | None:
    low = _norm(text)
    for key in ("30d", "14d", "7d", "3d", "1d"):
        pass
    if any(x in low for x in ("30 дн", "за месяц", "месяц", "30d")):
        return "30d"
    if any(x in low for x in ("14 дн", "две недел", "2 недел", "14d")):
        return "14d"
    if any(x in low for x in ("7 дн", "недел", "7d")):
        return "7d"
    if any(x in low for x in ("3 дн", "3d")):
        return "3d"
    if any(x in low for x in ("1 дн", "сегодня", "сутки", "1d")):
        return "1d"
    return None


# --------------------------------------------------------------------------- #
# Enums / models
# --------------------------------------------------------------------------- #

class TrendLabel(str, Enum):
    IMPROVING = "IMPROVING"
    DECLINING = "DECLINING"
    STABLE = "STABLE"
    VOLATILE = "VOLATILE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class DynCase(str, Enum):
    """Causal funnel over dynamics — aligns with FunnelCase A–D."""
    A_CTR_DROP = "A_CTR_DROP"           # CTR↓ CVR stable → entry
    B_CVR_DROP = "B_CVR_DROP"           # CTR stable CVR↓ → after click
    C_BOTH_DROP = "C_BOTH_DROP"         # both↓ → two stage
    D_PROFIT_DROP = "D_PROFIT_DROP"     # funnel OK, profit/margin↓
    SALES_DROP = "SALES_DROP"           # orders/revenue↓ without clear funnel
    PRICE_SHIFT = "PRICE_SHIFT"         # price moved; causation cautious
    RATING_DROP = "RATING_DROP"         # rating/fb dynamics
    STOCK_RISK = "STOCK_RISK"           # stock/procurement signal
    HEALTHY_STABLE = "HEALTHY_STABLE"   # stable + healthy → NO_ACTION
    INSUFFICIENT = "INSUFFICIENT"
    CHECK = "CHECK"


@dataclass
class MetricPoint:
    """One timestamped metric vector. Articles never mixed externally."""
    captured_at: float
    period: str | None = None
    price: float | None = None
    rating: float | None = None
    feedbacks: int | None = None
    impressions: float | None = None
    views: float | None = None
    clicks: float | None = None
    ctr: float | None = None
    orders: float | None = None
    sales: float | None = None
    cvr: float | None = None
    revenue: float | None = None
    costs: float | None = None
    profit: float | None = None
    margin: float | None = None
    stock: float | None = None
    ad_spend: float | None = None
    cost: float | None = None
    returns: float | None = None
    source: str | None = None
    confidence: float | None = None
    provenance: dict[str, str] = field(default_factory=dict)
    quality: str = "VALID"
    quality_reasons: list[str] = field(default_factory=list)

    def get(self, key: str) -> float | None:
        v = getattr(self, key, None)
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class MetricDelta:
    metric: str
    previous: float | None
    current: float | None
    abs_delta: float | None
    rel_delta: float | None          # fraction, e.g. -0.12 = -12%
    pp_delta: float | None           # percentage points for rate metrics
    label: str                       # human: «−0.4 п.п.» or «−12%»
    trend: TrendLabel = TrendLabel.INSUFFICIENT_DATA

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "previous": self.previous,
            "current": self.current,
            "abs_delta": self.abs_delta,
            "rel_delta": self.rel_delta,
            "pp_delta": self.pp_delta,
            "label": self.label,
            "trend": self.trend.value,
        }


@dataclass
class Forecast:
    metric: str
    low: float | None
    mid: float | None
    high: float | None
    confidence: float
    note: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DynamicsContext:
    article: int | None = None
    product_title: str | None = None
    period: str = "7d"
    last_case: str | None = None
    last_summary: str | None = None
    provenance: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "DynamicsContext":
        if not data:
            return cls()
        allowed = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in allowed}
        ctx = cls(**kwargs)
        if not isinstance(ctx.provenance, dict):
            ctx.provenance = {}
        return ctx


@dataclass
class DynamicsDiagnosis:
    case: DynCase
    title: str
    why: str
    period: str
    confidence: float
    confidence_band: str
    confidence_why: str
    trends: dict[str, str] = field(default_factory=dict)   # metric → TrendLabel
    deltas: list[MetricDelta] = field(default_factory=list)
    forecasts: list[Forecast] = field(default_factory=list)
    do_first: list[str] = field(default_factory=list)
    leave_alone: list[str] = field(default_factory=list)
    check: list[str] = field(default_factory=list)
    figures: list[str] = field(default_factory=list)
    funnel_case: str | None = None
    action_class: str = "CHECK"  # NO_ACTION | CHECK | ACTION | INSUFFICIENT_DATA
    n_points: int = 0
    historical_available: bool = False
    honesty: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case": self.case.value,
            "title": self.title,
            "why": self.why,
            "period": self.period,
            "confidence": self.confidence,
            "confidence_band": self.confidence_band,
            "confidence_why": self.confidence_why,
            "trends": dict(self.trends),
            "deltas": [d.to_dict() for d in self.deltas],
            "forecasts": [f.to_dict() for f in self.forecasts],
            "do_first": list(self.do_first),
            "leave_alone": list(self.leave_alone),
            "check": list(self.check),
            "figures": list(self.figures),
            "funnel_case": self.funnel_case,
            "action_class": self.action_class,
            "n_points": self.n_points,
            "historical_available": self.historical_available,
            "honesty": list(self.honesty),
        }


@dataclass
class DynamicsTurnResult:
    text: str
    diagnosis: DynamicsDiagnosis
    ctx: DynamicsContext
    clarify: str | None = None


# --------------------------------------------------------------------------- #
# Snapshot ↔ MetricPoint
# --------------------------------------------------------------------------- #

_POINT_KEYS = (
    "price", "rating", "feedbacks", "impressions", "views", "clicks", "ctr",
    "orders", "sales", "cvr", "revenue", "costs", "profit", "margin", "stock",
    "ad_spend", "cost", "returns",
)


def snapshot_to_point(snap: Any) -> MetricPoint:
    """Accept ProductMetricSnapshot or dict-like."""
    def _g(name: str, default=None):
        if isinstance(snap, dict):
            return snap.get(name, default)
        return getattr(snap, name, default)

    prov_raw = _g("provenance")
    prov: dict[str, str] = {}
    if isinstance(prov_raw, dict):
        prov = {str(k): str(v) for k, v in prov_raw.items()}
    elif isinstance(prov_raw, str) and prov_raw.strip():
        try:
            loaded = json.loads(prov_raw)
            if isinstance(loaded, dict):
                prov = {str(k): str(v) for k, v in loaded.items()}
        except Exception:
            pass

    kwargs: dict[str, Any] = {
        "captured_at": float(_g("captured_at") or time.time()),
        "period": _g("period"),
        "source": _g("source"),
        "confidence": _g("confidence"),
        "provenance": prov,
    }
    for k in _POINT_KEYS:
        kwargs[k] = _g(k)
    try:
        from backend.ai.funnel_consistency import validate_funnel_fields
        res = validate_funnel_fields(
            impressions=kwargs.get("impressions"),
            clicks=kwargs.get("clicks"),
            orders=kwargs.get("orders"),
            ctr=kwargs.get("ctr"),
            cvr=kwargs.get("cvr"),
        )
        kwargs["quality"] = res.quality
        kwargs["quality_reasons"] = list(res.reasons)
        if not res.is_ok:
            stamped = dict(prov)
            stamped["_funnel_quality"] = res.quality
            kwargs["provenance"] = stamped
    except Exception:
        kwargs["quality"] = "VALID"
        kwargs["quality_reasons"] = []
    return MetricPoint(**kwargs)


def usable_points_for_dynamics(points: Sequence[MetricPoint]) -> list[MetricPoint]:
    """Exclude INVALID/INCONSISTENT snapshots from forecast and causal trend."""
    from backend.ai.funnel_consistency import validate_funnel_fields
    out: list[MetricPoint] = []
    for p in points:
        q = str(getattr(p, "quality", None) or "VALID").upper()
        if q in ("INVALID", "INCONSISTENT"):
            continue
        res = validate_funnel_fields(
            impressions=p.impressions,
            clicks=p.clicks,
            orders=p.orders,
            ctr=p.ctr,
            cvr=p.cvr,
        )
        if not res.is_ok:
            continue
        out.append(p)
    return out


def build_series_from_snapshots(rows: Sequence[Any]) -> list[MetricPoint]:
    """Oldest→newest. Caller must already filter by one article."""
    points = [snapshot_to_point(r) for r in rows]
    points.sort(key=lambda p: p.captured_at)
    return points


def filter_series_by_period(
    points: Sequence[MetricPoint],
    period: str,
    *,
    now: float | None = None,
) -> list[MetricPoint]:
    now = float(now if now is not None else time.time())
    window = PERIODS_SEC.get(period, PERIODS_SEC["7d"])
    since = now - window
    return [p for p in points if p.captured_at >= since]


# --------------------------------------------------------------------------- #
# Build snapshot payload from seller / product / finance
# --------------------------------------------------------------------------- #

def build_snapshot_payload(
    *,
    seller_data=None,
    product=None,
    finance_ctx=None,
    unit_econ: dict | None = None,
    source: str = "session",
    period: str | None = None,
    captured_at: float | None = None,
) -> dict[str, Any]:
    """
    Assemble one snapshot dict + provenance. Never invents metrics.
    Card feedbacks tagged separately from seller private metrics.
    """
    prov: dict[str, str] = {}
    out: dict[str, Any] = {
        "captured_at": float(captured_at if captured_at is not None else time.time()),
        "period": period or (getattr(seller_data, "period", None) if seller_data else None),
        "source": source,
        "confidence": None,
    }

    def _set(key: str, value: Any, src: str) -> None:
        if value is None:
            return
        out[key] = value
        prov[key] = src

    if seller_data is not None:
        for key, attr in (
            ("price", "price"),
            ("rating", "rating"),
            ("feedbacks", "feedbacks"),
            ("impressions", "impressions"),
            ("views", "views"),
            ("ctr", "ctr"),
            ("orders", "orders"),
            ("sales", "sales"),
            ("cvr", "cvr"),
            ("ad_spend", "ad_spend"),
            ("cost", "cost"),
            ("returns", "returns"),
        ):
            val = getattr(seller_data, attr, None)
            src_attr = f"{attr}_source"
            src = getattr(seller_data, src_attr, None) or "seller"
            _set(key, val, str(src))
        # clicks proxy from views (explicit)
        if out.get("clicks") is None and getattr(seller_data, "views", None) is not None:
            _set("clicks", getattr(seller_data, "views"), "assumed:views_as_clicks")

    # Card overlays only when seller field missing — provenance = card
    if product is not None:
        if out.get("price") is None and getattr(product, "price", None) is not None:
            _set("price", float(product.price), "card")
        if out.get("rating") is None and getattr(product, "rating", None) is not None:
            _set("rating", float(product.rating), "card")
        if out.get("feedbacks") is None and getattr(product, "feedbacks", None) is not None:
            _set("feedbacks", int(product.feedbacks), "card_fb")  # ≠ processed reviews
        stock = getattr(product, "total_qty", None)
        if stock is None:
            sizes = getattr(product, "sizes", None) or []
            if sizes and hasattr(sizes[0], "qty"):
                try:
                    stock = sum(int(getattr(s, "qty", 0) or 0) for s in sizes)
                except Exception:
                    stock = None
        if stock is not None:
            _set("stock", int(stock), "card")

    # Derived economics
    ue = unit_econ
    if ue is None and seller_data is not None:
        try:
            ue = compute_unit_economics(seller_data, product)
        except Exception:
            ue = None
    if isinstance(ue, dict) and ue.get("complete"):
        if ue.get("contribution") is not None:
            _set("profit", float(ue["contribution"]), "computed:unit_econ")
        if ue.get("margin_pct") is not None:
            _set("margin", float(ue["margin_pct"]), "computed:unit_econ")
        price = out.get("price")
        orders = out.get("orders")
        if price is not None and orders is not None:
            _set("revenue", float(price) * float(orders), "computed:price*orders")

    if finance_ctx is not None:
        try:
            from backend.ai.finance_planner import calculate
            calc = calculate(finance_ctx)
            if calc.revenue is not None and out.get("revenue") is None:
                _set("revenue", float(calc.revenue), "computed:finance")
            profit = calc.net_profit
            if profit is None:
                profit = calc.after_ads or calc.after_commission or calc.gross_profit
            if profit is not None and out.get("profit") is None:
                _set("profit", float(profit), "computed:finance")
            if calc.margin_pct is not None and out.get("margin") is None:
                _set("margin", float(calc.margin_pct), "computed:finance")
            if calc.cogs_known_total is not None:
                _set("costs", float(calc.cogs_known_total), "computed:finance")
        except Exception:
            pass

    # confidence: share of known core fields
    core = ("ctr", "cvr", "orders", "impressions", "price")
    known_n = sum(1 for k in core if out.get(k) is not None)
    out["confidence"] = round(known_n / len(core), 2)
    out["provenance"] = prov
    return out


# --------------------------------------------------------------------------- #
# Deltas / trends
# --------------------------------------------------------------------------- #

def compute_delta(metric: str, previous: float | None, current: float | None) -> MetricDelta:
    if previous is None or current is None:
        return MetricDelta(
            metric=metric,
            previous=previous,
            current=current,
            abs_delta=None,
            rel_delta=None,
            pp_delta=None,
            label="нет данных",
            trend=TrendLabel.INSUFFICIENT_DATA,
        )
    abs_d = current - previous
    rel = None
    pp = None
    if metric in RATE_METRICS:
        pp = abs_d
        label = f"{pp:+.2f} п.п."
        if abs(pp) <= STABLE_PP:
            trend = TrendLabel.STABLE
        elif pp > 0:
            trend = TrendLabel.IMPROVING if metric != "cost" else TrendLabel.DECLINING
        else:
            # lower CTR/CVR/margin/rating = declining (except costs handled elsewhere)
            trend = TrendLabel.DECLINING
        # for cost-like rates none here
    else:
        if abs(previous) > 1e-9:
            rel = abs_d / abs(previous)
            label = f"{rel*100:+.1f}%"
        else:
            label = f"{abs_d:+.2f}"
        if rel is not None and abs(rel) <= STABLE_REL:
            trend = TrendLabel.STABLE
        elif abs_d > 0:
            # higher sales/orders/revenue/profit/stock = improving; higher cost = declining
            if metric in ("costs", "cost", "ad_spend", "returns"):
                trend = TrendLabel.DECLINING
            else:
                trend = TrendLabel.IMPROVING
        else:
            if metric in ("costs", "cost", "ad_spend", "returns"):
                trend = TrendLabel.IMPROVING
            else:
                trend = TrendLabel.DECLINING
    return MetricDelta(
        metric=metric,
        previous=previous,
        current=current,
        abs_delta=abs_d,
        rel_delta=rel,
        pp_delta=pp,
        label=label,
        trend=trend,
    )


def classify_series_trend(values: Sequence[float | None]) -> TrendLabel:
    clean = [float(v) for v in values if v is not None]
    if len(clean) < MIN_POINTS_TREND:
        return TrendLabel.INSUFFICIENT_DATA
    if len(clean) == 2:
        # two points only — never call systemic; mild direction ok
        d = compute_delta("x", clean[0], clean[1])
        if d.trend is TrendLabel.STABLE:
            return TrendLabel.STABLE
        # mark as direction but consumer must not treat as systemic
        return d.trend
    # volatility: sign changes of consecutive deltas — only if moves are material
    deltas = [clean[i + 1] - clean[i] for i in range(len(clean) - 1)]
    if not deltas:
        return TrendLabel.INSUFFICIENT_DATA
    base = abs(clean[0]) if abs(clean[0]) > 1e-9 else 1.0
    material = [d for d in deltas if abs(d) / base > STABLE_REL]
    signs = [1 if d > 0 else (-1 if d < 0 else 0) for d in material]
    nonzero = [s for s in signs if s != 0]
    flips = sum(1 for i in range(len(nonzero) - 1) if nonzero[i] != nonzero[i + 1])
    if len(nonzero) >= 3 and flips >= 2:
        return TrendLabel.VOLATILE
    overall = clean[-1] - clean[0]
    mean_abs = sum(abs(d) for d in deltas) / len(deltas)
    if abs(overall) / base <= STABLE_REL and mean_abs / base <= STABLE_REL * 1.5:
        return TrendLabel.STABLE
    return TrendLabel.IMPROVING if overall > 0 else TrendLabel.DECLINING


def period_endpoints(
    points: Sequence[MetricPoint],
    period: str,
    *,
    now: float | None = None,
) -> tuple[MetricPoint | None, MetricPoint | None]:
    """Previous ≈ oldest in window (or just before), current ≈ newest."""
    series = filter_series_by_period(points, period, now=now)
    if not series:
        # fall back to full series endpoints
        if len(points) >= 1:
            return (points[0] if len(points) >= 2 else None, points[-1])
        return None, None
    if len(series) == 1 and len(points) >= 2:
        # compare to last point before window
        before = [p for p in points if p.captured_at < series[0].captured_at]
        prev = before[-1] if before else None
        return prev, series[-1]
    if len(series) == 1:
        return None, series[-1]
    return series[0], series[-1]


def compute_period_deltas(
    points: Sequence[MetricPoint],
    period: str,
    metrics: Sequence[str] | None = None,
    *,
    now: float | None = None,
) -> list[MetricDelta]:
    metrics = list(metrics or (
        "ctr", "cvr", "orders", "sales", "impressions", "clicks",
        "revenue", "profit", "margin", "price", "rating", "feedbacks", "stock",
    ))
    prev, cur = period_endpoints(points, period, now=now)
    out: list[MetricDelta] = []
    for m in metrics:
        p = prev.get(m) if prev else None
        c = cur.get(m) if cur else None
        d = compute_delta(m, p, c)
        # enrich trend from full series when ≥3 points
        vals = [pt.get(m) for pt in filter_series_by_period(points, period, now=now)]
        series_trend = classify_series_trend(vals)
        if series_trend is not TrendLabel.INSUFFICIENT_DATA:
            if len([v for v in vals if v is not None]) >= MIN_POINTS_SYSTEMIC:
                d.trend = series_trend
            # else keep 2-point direction but caller won't mark systemic
        out.append(d)
    return out


def format_period_comparison_table(
    deltas: Sequence[MetricDelta],
    *,
    max_rows: int = 10,
) -> list[str]:
    """
    Было → Стало table. Only metrics with both endpoints.
    Rate metrics → п.п.; volumes → relative %.
    """
    rows: list[str] = []
    for d in deltas:
        if d.previous is None or d.current is None:
            continue
        name = d.metric.upper() if d.metric in RATE_METRICS else d.metric
        if d.pp_delta is not None:
            rows.append(
                f"• {name}: {d.previous:.2f} → {d.current:.2f}  ({d.label})"
            )
        elif d.rel_delta is not None:
            rows.append(
                f"• {name}: {d.previous:.2f} → {d.current:.2f}  ({d.label})"
            )
        else:
            rows.append(
                f"• {name}: {d.previous:.2f} → {d.current:.2f}  ({d.label})"
            )
        if len(rows) >= max_rows:
            break
    if not rows:
        return []
    return ["Сравнение периода (было → стало):", *rows]


def honesty_for_n_points(n: int) -> list[str]:
    """Explicit honesty ladder: 1 / 2 / 3+ / 4+."""
    if n <= 0:
        return ["historical data unavailable — снимков нет."]
    if n == 1:
        return [
            "1 снимок = точка «сейчас», не динамика. Нужен ещё один срез.",
        ]
    if n == 2:
        return [
            "2 точки = осторожное направление, не системный тренд.",
            "2 точки ≠ системная проблема.",
        ]
    if n == 3:
        return [
            "3+ точки: направление можно считать повторяемым, но прогноз ещё рано.",
        ]
    return [
        "4+ качественных среза: можно дать осторожный ориентир-прогноз (не гарантия).",
    ]


def count_quality_points(
    points: Sequence[MetricPoint],
    metric: str,
    *,
    min_confidence: float = 0.3,
) -> int:
    """Points where metric is measured (not None) and confidence OK if set."""
    n = 0
    for p in points:
        if p.get(metric) is None:
            continue
        q = str(getattr(p, "quality", None) or "VALID").upper()
        if q in ("INVALID", "INCONSISTENT"):
            continue
        if p.confidence is not None and float(p.confidence) < min_confidence:
            continue
        # reject synthetic provenance for production forecasts
        src = (p.provenance or {}).get(metric) or (p.source or "")
        if str(src).lower().startswith("synthetic"):
            continue
        n += 1
    return n


def soft_review_cvr_note(
    *,
    case: DynCase,
    review_risks: Sequence[Any] | None,
) -> str | None:
    """Soft correlation only — never «отзывы вызвали»."""
    if not review_risks:
        return None
    if case not in (DynCase.B_CVR_DROP, DynCase.C_BOTH_DROP, DynCase.RATING_DROP):
        return None
    return (
        "Падение CVR/рейтинга согласуется с гипотезой по отзывам — "
        "не утверждаю, что отзывы вызвали просадку."
    )


def decision_update_note(prev_case: str | None, new_case: DynCase) -> str | None:
    """Update diagnosis vs prior advice — don't blindly repeat."""
    if not prev_case:
        return None
    if prev_case == new_case.value:
        return None
    return (
        f"Раньше: {prev_case} → сейчас: {new_case.value}. "
        "Обновляю диагноз по новым срезам, не повторяю старый совет вслепую."
    )


# --------------------------------------------------------------------------- #
# Forecast (simple linear; honest insufficient)
# --------------------------------------------------------------------------- #

def simple_forecast(
    points: Sequence[MetricPoint],
    metric: str,
    *,
    horizon_days: float = 7.0,
    allow_synthetic: bool = False,
) -> Forecast:
    vals: list[tuple[float, float]] = []
    for p in points:
        v = p.get(metric)
        if v is None:
            continue
        src = str((p.provenance or {}).get(metric) or (p.source or "")).lower()
        if src.startswith("synthetic") and not allow_synthetic:
            continue
        q = str(getattr(p, "quality", None) or "VALID").upper()
        if q in ("INVALID", "INCONSISTENT"):
            continue
        if (
            p.confidence is not None
            and float(p.confidence) < 0.3
            and not allow_synthetic
        ):
            continue
        vals.append((p.captured_at, float(v)))
    if len(vals) < MIN_POINTS_FORECAST:
        return Forecast(
            metric=metric,
            low=None,
            mid=None,
            high=None,
            confidence=0.0,
            note="historical data unavailable — нужно ≥4 качественных среза для прогноза",
        )
    # linear regression y = a + b*t
    ts = [v[0] for v in vals]
    ys = [float(v[1]) for v in vals]  # type: ignore[arg-type]
    t0 = ts[0]
    xs = [(t - t0) / 86400.0 for t in ts]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    var_x = sum((x - mean_x) ** 2 for x in xs)
    if var_x < 1e-12:
        return Forecast(
            metric=metric,
            low=None,
            mid=None,
            high=None,
            confidence=0.0,
            note="historical data unavailable — нет разброса по времени",
        )
    cov = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    b = cov / var_x
    a = mean_y - b * mean_x
    x_h = xs[-1] + horizon_days
    mid = a + b * x_h
    resid = [ys[i] - (a + b * xs[i]) for i in range(n)]
    rmse = math.sqrt(sum(r * r for r in resid) / n)
    # confidence shrinks with volatility
    scale = abs(mean_y) if abs(mean_y) > 1e-9 else 1.0
    conf = max(0.15, min(0.75, 1.0 - (rmse / scale)))
    return Forecast(
        metric=metric,
        low=mid - 1.5 * rmse,
        mid=mid,
        high=mid + 1.5 * rmse,
        confidence=round(conf, 2),
        note=f"линейный ориентир на {horizon_days:.0f}д (не гарантия)",
    )


# --------------------------------------------------------------------------- #
# Causal diagnosis over dynamics
# --------------------------------------------------------------------------- #

def _delta_map(deltas: Sequence[MetricDelta]) -> dict[str, MetricDelta]:
    return {d.metric: d for d in deltas}


def _is_drop(d: MetricDelta | None, *, pp: float | None = None, rel: float | None = None) -> bool:
    if d is None or d.current is None or d.previous is None:
        return False
    if d.pp_delta is not None and pp is not None:
        return d.pp_delta <= -pp
    if d.rel_delta is not None and rel is not None:
        return d.rel_delta <= -rel
    return d.trend is TrendLabel.DECLINING


def _is_stable(d: MetricDelta | None) -> bool:
    return d is not None and d.trend is TrendLabel.STABLE


def _is_up(d: MetricDelta | None) -> bool:
    return d is not None and d.trend is TrendLabel.IMPROVING


def diagnose_dynamics(
    points: Sequence[MetricPoint],
    *,
    period: str = "7d",
    seller_data=None,
    product=None,
    finance_ctx=None,
    unit_econ: dict | None = None,
    now: float | None = None,
    card_healthy: bool = False,
    review_risks: Sequence[Any] | None = None,
    prev_case: str | None = None,
    allow_synthetic_forecast: bool = False,
) -> DynamicsDiagnosis:
    """
    Main diagnosis: deltas + causal Cases A–D over time.
    Never marks systemic on <3 points.
    """
    honesty: list[str] = []
    retained = list(points)
    points = usable_points_for_dynamics(points)
    excluded = len(retained) - len(points)
    n = len(points)
    honesty.extend(honesty_for_n_points(n))
    if excluded:
        honesty.append(
            f"{excluded} срез(ов) с quality=INVALID/INCONSISTENT исключены "
            "из тренда и прогноза (сохранены для audit)."
        )
    if n < MIN_POINTS_TREND:
        return DynamicsDiagnosis(
            case=DynCase.INSUFFICIENT,
            title="Истории пока нет" if n == 0 else "Одна точка — динамики ещё нет",
            why=(
                "historical data unavailable — нужно ≥2 снимка метрик по этому артикулу."
                if n == 0
                else "1 снимок = факт «сейчас», не тренд. Сохрани ещё один срез через 1–3 дня."
            ),
            period=period,
            confidence=0.2,
            confidence_band="низкая",
            confidence_why="мало снимков",
            action_class="INSUFFICIENT_DATA",
            n_points=n,
            historical_available=False,
            honesty=honesty + ["Не выдумываю прошлые CTR/продажи."],
            check=["снять ещё один срез seller-метрик через 1–3 дня"],
            do_first=["Сохрани текущие CTR/CVR/заказы — это станет точкой «было»"],
            leave_alone=["не менять карточку «на всякий» без динамики"],
            figures=[],
        )

    deltas = compute_period_deltas(points, period, now=now)
    dm = _delta_map(deltas)
    trends = {d.metric: d.trend.value for d in deltas if d.trend is not TrendLabel.INSUFFICIENT_DATA}
    figures = format_period_comparison_table(deltas)

    # Point-in-time funnel case (current) — skip causal if input inconsistent
    funnel_case = None
    try:
        from backend.ai.funnel_consistency import validate_seller_funnel
        cons = validate_seller_funnel(seller_data)
        if not cons.is_ok:
            funnel_case = "INCONSISTENT"
            honesty.append("текущие данные воронки противоречивы — funnel case не считаю")
        else:
            fm = compute_funnel_metrics(seller_data=seller_data)
            if fm.ctr is None and dm.get("ctr") and dm["ctr"].current is not None:
                fm.ctr = dm["ctr"].current
                fm.ctr_status = (
                    MetricStatus.LOW if fm.ctr < 2.0 else MetricStatus.OK
                )
                fm.ctr_source = "dynamics"
            if fm.cvr is None and dm.get("cvr") and dm["cvr"].current is not None:
                fm.cvr = dm["cvr"].current
                fm.cvr_status = (
                    MetricStatus.LOW if fm.cvr < 5.0 else MetricStatus.OK
                )
                fm.cvr_source = "dynamics"
            ue = unit_econ or (
                compute_unit_economics(seller_data, product) if seller_data is not None else None
            )
            fd = diagnose_funnel(fm, unit=ue, product=product)
            funnel_case = fd.case.value
    except Exception:
        funnel_case = None

    systemic_ok = n >= MIN_POINTS_SYSTEMIC
    # honesty_for_n_points already covers 2-point caution

    ctr_d, cvr_d = dm.get("ctr"), dm.get("cvr")
    profit_d = dm.get("profit") or dm.get("margin")
    orders_d = dm.get("orders") or dm.get("sales") or dm.get("revenue")
    price_d = dm.get("price")
    rating_d = dm.get("rating")
    stock_d = dm.get("stock")

    case = DynCase.CHECK
    title = "Динамика: нужна проверка"
    why = "Есть изменения, но причинно-следственная связь ещё слабая."
    do_first: list[str] = []
    leave_alone: list[str] = []
    check: list[str] = []
    action = "CHECK"
    conf = 0.45
    conf_why = "мало подтверждений"

    # Case A: CTR drop, CVR stable
    if _is_drop(ctr_d, pp=CTR_DROP_PP) and (
        _is_stable(cvr_d) or (cvr_d and cvr_d.pp_delta is not None and cvr_d.pp_delta >= -CVR_DROP_PP)
    ):
        case = DynCase.A_CTR_DROP
        title = "Падение на входе (CTR↓, CVR стабилен)"
        why = (
            f"CTR {ctr_d.label} при относительно стабильном CVR — "
            "похоже на проблему входа (фото/название/реклама), не карточки после клика."
        )
        do_first = [
            "Проверить главное фото и название выдачи",
            "Сверить ставки/охват рекламы с прошлым периодом",
        ]
        leave_alone = ["не переписывать описание «вслепую» — CVR не просел"]
        action = "ACTION" if systemic_ok else "CHECK"
        conf = 0.7 if systemic_ok else 0.45
        conf_why = "паттерн Case A (как Funnel A_ENTRY) по динамике" if systemic_ok else "мало точек"

    # Case B: CTR stable, CVR drop
    elif _is_drop(cvr_d, pp=CVR_DROP_PP) and (
        _is_stable(ctr_d) or (ctr_d and ctr_d.pp_delta is not None and ctr_d.pp_delta >= -CTR_DROP_PP)
    ):
        case = DynCase.B_CVR_DROP
        title = "Падение после клика (CVR↓, CTR стабилен)"
        why = (
            f"CVR {cvr_d.label} при стабильном CTR — смотреть карточку/цену/отзывы после клика."
        )
        do_first = [
            "Сверить цену и офферы с прошлым периодом",
            "Проверить свежие жалобы в отзывах (не путать card feedbacks с RI)",
        ]
        leave_alone = ["не крутить рекламу сильнее — вход (CTR) не сломан"]
        action = "ACTION" if systemic_ok else "CHECK"
        conf = 0.7 if systemic_ok else 0.45
        conf_why = "паттерн Case B (Funnel B_AFTER_CLICK)" if systemic_ok else "мало точек"

    # Case C: both drop
    elif _is_drop(ctr_d, pp=CTR_DROP_PP) and _is_drop(cvr_d, pp=CVR_DROP_PP):
        case = DynCase.C_BOTH_DROP
        title = "Два этапа просели (CTR↓ и CVR↓)"
        why = "И вход, и конверсия после клика хуже — сначала стабилизировать CTR, потом CVR."
        do_first = [
            "Сначала вход: фото/название/реклама",
            "Затем карточка/цена/отзывы",
        ]
        leave_alone = ["не менять всё сразу — иначе не измерить эффект"]
        action = "ACTION" if systemic_ok else "CHECK"
        conf = 0.65 if systemic_ok else 0.4
        conf_why = "паттерн Case C" if systemic_ok else "мало точек"

    # Case D: funnel OK-ish, profit/margin down
    elif (
        (not _is_drop(ctr_d, pp=CTR_DROP_PP) and not _is_drop(cvr_d, pp=CVR_DROP_PP))
        and _is_drop(profit_d, pp=1.0 if profit_d and profit_d.pp_delta is not None else None,
                     rel=REL_SALES_DROP)
    ):
        case = DynCase.D_PROFIT_DROP
        title = "Воронка держится, экономика слабеет"
        why = (
            "CTR/CVR без явного провала, но прибыль/маржа хуже — unit economics / закупка / ДРР. "
            "Маржа % ≠ наценка % (прибыль/выручка vs прибыль/себестоимость)."
        )
        do_first = [
            "Пересчитать юнит-экономику (себест./комиссия/реклама)",
            "Сверить закупку и рекламный расход с прошлым периодом",
        ]
        leave_alone = ["не ломать здоровую воронку ради «оптимизации»"]
        action = "ACTION" if systemic_ok else "CHECK"
        conf = 0.65 if systemic_ok else 0.4
        conf_why = "паттерн Case D (Funnel D_UNIT_ECON)" if systemic_ok else "мало точек"
        honesty.append("Маржа ≠ наценка — цифры подписываю отдельно.")
        # break-even via finance planner when context present
        if finance_ctx is not None:
            try:
                from backend.ai.finance_planner import calculate
                calc = calculate(finance_ctx)
                if calc.units_to_breakeven is not None:
                    do_first.append(
                        f"До безубыточности ориентир ≈ {calc.units_to_breakeven:.0f} шт "
                        "(через finance planner; при неполных данных — честно partial)"
                    )
                elif calc.missing or calc.not_included:
                    miss = list(calc.missing or []) + list(calc.not_included or [])
                    honesty.append(
                        "Экономика неполная: " + ", ".join(miss[:4]) + " — break-even не выдумываю."
                    )
            except Exception:
                pass

    # Sales drop without clear funnel
    elif _is_drop(orders_d, rel=REL_SALES_DROP):
        case = DynCase.SALES_DROP
        title = "Продажи/выручка просели"
        why = "Заказы или выручка ниже прошлого среза; воронка не даёт однозначного locus."
        do_first = ["Снять CTR/CVR за тот же период — иначе диагноз неполный"]
        check = ["показы", "остатки", "сезонность"]
        action = "CHECK"
        conf = 0.5 if systemic_ok else 0.35
        conf_why = "есть sales delta, нет полной воронки"

    # Price shift — cautious causation
    elif price_d and price_d.abs_delta is not None and abs(price_d.abs_delta) >= 1:
        case = DynCase.PRICE_SHIFT
        title = "Цена изменилась"
        why = (
            f"Цена {price_d.label}. Не утверждаю, что это причина продаж — "
            "нужны CTR/CVR и заказы в том же окне."
        )
        do_first = ["Сопоставить цену с CTR/CVR/заказами за тот же период"]
        leave_alone = ["не двигать цену повторно без замера"]
        action = "CHECK"
        honesty.append("Корреляция цены и продаж ≠ доказанная причинность.")
        conf = 0.4
        conf_why = "осторожная причинность по цене"

    # Rating / reviews dynamics
    elif _is_drop(rating_d, pp=0.1) or (
        dm.get("feedbacks") and _is_up(dm.get("feedbacks")) and _is_drop(orders_d, rel=0.1)
    ):
        case = DynCase.RATING_DROP
        title = "Рейтинг/отзывы в динамике"
        why = (
            "Рейтинг или объём card feedbacks изменился. "
            "Card feedbacks ≠ обработанные отзывы (RI) — не смешиваю."
        )
        do_first = ["Отделить card feedbacks от разбора жалоб RI"]
        honesty.append("card feedbacks ≠ processed review intelligence")
        action = "CHECK"
        conf = 0.45
        conf_why = "сигнал рейтинга/отзывов"

    # Stock risk via finance planner signals
    elif stock_d and (
        (stock_d.current is not None and stock_d.current <= 5)
        or _is_drop(stock_d, rel=0.3)
    ):
        case = DynCase.STOCK_RISK
        title = "Остатки / закупка — риск"
        why = "Остатки низкие или быстро тают. Закупку считаю через finance planner, без выдуманного спроса."
        do_first = []
        # call finance operational hints if context present
        if finance_ctx is not None:
            try:
                from backend.ai.finance_planner import calculate, operational_plan
                calc = calculate(finance_ctx)
                plan = operational_plan(finance_ctx, calc, demand_proven=False)
                do_first = plan[:3] if plan else ["Считать тестовую партию, не раздувать склад"]
            except Exception:
                do_first = ["Считать тестовую партию через финансовый калькулятор"]
        else:
            do_first = [
                "Открыть финансовый расчёт закупки",
                "Не заказывать крупную партию без доказанного спроса (CTR/CVR/заказы)",
            ]
        leave_alone = ["не закупать «на глаз» без unit economics"]
        action = "ACTION" if systemic_ok else "CHECK"
        conf = 0.55
        conf_why = "stock signal + finance caution"

    # Healthy stable → NO_ACTION
    elif (
        card_healthy
        or (
            (_is_stable(ctr_d) or ctr_d is None or ctr_d.trend is TrendLabel.INSUFFICIENT_DATA)
            and (_is_stable(cvr_d) or cvr_d is None or cvr_d.trend is TrendLabel.INSUFFICIENT_DATA)
            and (_is_stable(orders_d) or _is_up(orders_d) or orders_d is None
                 or orders_d.trend is TrendLabel.INSUFFICIENT_DATA)
        )
    ) and not _is_drop(orders_d, rel=REL_SALES_DROP):
        # require at least some stability evidence
        stable_hits = sum(
            1 for d in (ctr_d, cvr_d, orders_d, profit_d)
            if d and d.trend in (TrendLabel.STABLE, TrendLabel.IMPROVING)
        )
        if stable_hits >= 1 or card_healthy:
            case = DynCase.HEALTHY_STABLE
            title = "Динамика стабильная — системной проблемы не видно"
            why = "Ключевые метрики без устойчивого провала. NO_ACTION."
            do_first = ["Ничего критичного не менять — мониторить следующий срез"]
            leave_alone = ["не трогать карточку/цену/рекламу без нового сигнала"]
            action = "NO_ACTION"
            conf = 0.6 if systemic_ok else 0.4
            conf_why = "stable/healthy dynamics"

    # Soft review ↔ CVR correlation language
    rev_note = soft_review_cvr_note(case=case, review_risks=review_risks)
    if rev_note:
        honesty.append(rev_note)

    # Previous decision vs new evidence
    upd = decision_update_note(prev_case, case)
    if upd:
        honesty.append(upd)

    # Forecasts (honest: ≥4 quality points; synthetic only if explicitly allowed in tests)
    forecasts: list[Forecast] = []
    for m in ("orders", "revenue", "ctr"):
        forecasts.append(
            simple_forecast(
                points, m, horizon_days=7.0, allow_synthetic=allow_synthetic_forecast,
            )
        )

    band = (
        "высокая" if conf >= 0.7 else
        "средняя" if conf >= 0.45 else
        "низкая"
    )

    return DynamicsDiagnosis(
        case=case,
        title=title,
        why=why,
        period=period,
        confidence=conf,
        confidence_band=band,
        confidence_why=conf_why,
        trends=trends,
        deltas=deltas,
        forecasts=forecasts,
        do_first=do_first,
        leave_alone=leave_alone,
        check=check,
        figures=figures,
        funnel_case=funnel_case,
        action_class=action,
        n_points=n,
        historical_available=True,
        honesty=honesty,
    )


# --------------------------------------------------------------------------- #
# Formatting (human first screen)
# --------------------------------------------------------------------------- #

def format_dynamics_sections(d: DynamicsDiagnosis, *, optional_forecast: bool = True) -> list[str]:
    """Blocks suitable for Advisor first screen / chat."""
    blocks: list[str] = []
    dyn_lines = ["📉 ДИНАМИКА", f"Период: {d.period} · точек: {d.n_points}"]
    if not d.historical_available:
        dyn_lines.append("historical data unavailable")
        dyn_lines.append(d.why)
    else:
        dyn_lines.append(d.title)
        dyn_lines.append(d.why)
        for f in d.figures[:8]:
            dyn_lines.append(f)
        if d.funnel_case:
            dyn_lines.append(f"Воронка (сейчас): {d.funnel_case}")
        dyn_lines.append(
            f"Уверенность: {d.confidence_band} ({d.confidence:.0%}) — {d.confidence_why}"
        )
        for h in d.honesty[:3]:
            dyn_lines.append(f"⚠ {h}")
    blocks.append("\n".join(dyn_lines))

    if optional_forecast:
        fc_lines = ["🔮 ПРОГНОЗ"]
        usable = [f for f in d.forecasts if f.mid is not None]
        if not usable:
            fc_lines.append("historical data unavailable — прогноза нет (мало точек / качество)")
        else:
            for f in usable[:3]:
                fc_lines.append(
                    f"• {f.metric}: {f.low:.2f} … {f.mid:.2f} … {f.high:.2f} "
                    f"(conf {f.confidence:.0%}) — {f.note}"
                )
        blocks.append("\n".join(fc_lines))
    return blocks


def format_dynamics_reply(
    d: DynamicsDiagnosis,
    *,
    depth: str = "normal",
    product_title: str | None = None,
) -> str:
    """
    Seller first screen:
    PROBLEM → FIGURES → DO → DON'T → DYNAMICS → FORECAST(optional)
    No evidence dumps.
    """
    lines: list[str] = ["📉 ДИНАМИКА ARGUS"]
    if product_title:
        lines.append(f"Товар: {product_title}")
    lines.append("")

    # 🎯 PROBLEM
    lines.append("🎯 ПРОБЛЕМА")
    if d.action_class == "NO_ACTION" or d.case is DynCase.HEALTHY_STABLE:
        lines.append("Системной проблемы по динамике не видно (NO_ACTION).")
    elif d.case is DynCase.INSUFFICIENT or not d.historical_available:
        lines.append(d.title)
    else:
        lines.append(d.title)
    lines.append(d.why)
    lines.append("")

    # 📊 FIGURES — only known deltas
    lines.append("📊 ЦИФРЫ")
    if d.figures:
        for f in d.figures[:10]:
            lines.append(f)
    else:
        lines.append("Нет парных замеров для сравнения периода.")
    lines.append("")

    if depth == "short":
        if d.do_first:
            lines.append(f"Шаг: {d.do_first[0]}")
        for h in d.honesty[:2]:
            lines.append(f"⚠ {h}")
        text = "\n".join(lines)
        text = re.sub(r"evidence=[A-Za-z0-9_-]+", "", text)
        text = re.sub(r"frequency=\S+", "", text)
        return text.strip()

    # 🔧 DO
    lines.append("🔧 ЧТО ДЕЛАТЬ")
    if d.action_class == "NO_ACTION":
        lines.append("NO_ACTION — стабильно и без системного риска.")
    if d.do_first:
        for i, a in enumerate(d.do_first[:4], 1):
            lines.append(f"{i}. {a}")
    elif d.action_class != "NO_ACTION":
        lines.append("Пока нет доказанного шага — сначала снять недостающие метрики.")
    lines.append("")

    # 🚫 DON'T
    if d.leave_alone:
        lines.append("🚫 НЕ ТРОГАТЬ")
        for x in d.leave_alone[:3]:
            lines.append(f"• {x}")
        lines.append("")

    # 📉 DYNAMICS meta
    lines.append("📉 ДИНАМИКА")
    lines.append(f"Период: {d.period} · точек: {d.n_points}")
    if d.funnel_case:
        lines.append(f"Воронка (сейчас): {d.funnel_case}")
    lines.append(
        f"Уверенность: {d.confidence_band} ({d.confidence:.0%}) — {d.confidence_why}"
    )
    for h in d.honesty[:4]:
        lines.append(f"⚠ {h}")
    if d.check and depth == "deep":
        lines.append("Проверить: " + ", ".join(d.check[:4]))
    lines.append("")

    # 🔮 FORECAST optional
    lines.append("🔮 ПРОГНОЗ")
    usable = [f for f in d.forecasts if f.mid is not None]
    if not usable:
        lines.append("historical data unavailable — прогноза нет (нужно ≥4 качественных среза)")
    else:
        for f in usable[:3]:
            lines.append(
                f"• {f.metric}: {f.low:.2f} … {f.mid:.2f} … {f.high:.2f} "
                f"(conf {f.confidence:.0%}) — {f.note}"
            )

    text = "\n".join(lines)
    text = re.sub(r"evidence=[A-Za-z0-9_-]+", "", text)
    text = re.sub(r"frequency=\S+", "", text)
    return text.strip()


def attach_dynamics_metadata(
    points: Sequence[MetricPoint] | None,
    *,
    period: str = "7d",
    seller_data=None,
    product=None,
    finance_ctx=None,
    card_healthy: bool = False,
    review_risks: Sequence[Any] | None = None,
    prev_case: str | None = None,
    allow_synthetic_forecast: bool = False,
) -> dict[str, Any] | None:
    """Thin advisor metadata hook — never rewrites bottleneck ranking."""
    pts = list(points or [])
    if not pts:
        return {
            "historical_available": False,
            "n_points": 0,
            "message": "historical data unavailable",
            "period": period,
        }
    diag = diagnose_dynamics(
        pts,
        period=period,
        seller_data=seller_data,
        product=product,
        finance_ctx=finance_ctx,
        card_healthy=card_healthy,
        review_risks=review_risks,
        prev_case=prev_case,
        allow_synthetic_forecast=allow_synthetic_forecast,
    )
    return diag.to_dict()


def first_screen_dynamics_blocks(meta: dict[str, Any] | None) -> list[str]:
    """Optional 📉 / 🔮 blocks for AdvisorPlan.format_plain."""
    if not meta:
        return []
    # Optional: stay quiet on first screen without real history (chat path is honest).
    if not meta.get("historical_available") and int(meta.get("n_points") or 0) < 2:
        return []
    # rebuild lightweight diagnosis view from meta
    try:
        case = DynCase(meta.get("case") or DynCase.INSUFFICIENT.value)
    except Exception:
        case = DynCase.INSUFFICIENT
    d = DynamicsDiagnosis(
        case=case,
        title=str(meta.get("title") or ""),
        why=str(meta.get("why") or ""),
        period=str(meta.get("period") or "7d"),
        confidence=float(meta.get("confidence") or 0),
        confidence_band=str(meta.get("confidence_band") or "низкая"),
        confidence_why=str(meta.get("confidence_why") or ""),
        figures=list(meta.get("figures") or []),
        forecasts=[
            Forecast(
                metric=f.get("metric", ""),
                low=f.get("low"),
                mid=f.get("mid"),
                high=f.get("high"),
                confidence=float(f.get("confidence") or 0),
                note=str(f.get("note") or ""),
            )
            for f in (meta.get("forecasts") or [])
            if isinstance(f, dict)
        ],
        funnel_case=meta.get("funnel_case"),
        action_class=str(meta.get("action_class") or "CHECK"),
        n_points=int(meta.get("n_points") or 0),
        historical_available=bool(meta.get("historical_available")),
        honesty=list(meta.get("honesty") or []),
        do_first=list(meta.get("do_first") or []),
        leave_alone=list(meta.get("leave_alone") or []),
    )
    return format_dynamics_sections(d, optional_forecast=True)


def answer_why_ctr(meta: dict[str, Any] | None, snap: dict[str, Any] | None = None) -> str | None:
    """Discussion memory: «почему CTR?» from dynamics + diagnosis snapshot."""
    lines = ["Почему CTR (из динамики / последнего разбора):"]
    if meta and meta.get("historical_available"):
        case = meta.get("case")
        why = meta.get("why") or ""
        lines.append(f"• Кейс динамики: {case}")
        if why:
            lines.append(f"• {why[:220]}")
        for fig in (meta.get("figures") or []):
            if "CTR" in str(fig).upper() or "ctr" in str(fig):
                lines.append(str(fig))
                break
        if meta.get("funnel_case"):
            lines.append(f"• Воронка сейчас: {meta.get('funnel_case')}")
        if meta.get("action_class") == "NO_ACTION":
            lines.append("• Сейчас NO_ACTION — CTR без системного провала.")
        return "\n".join(lines)
    if snap:
        main = snap.get("main_problem") or snap.get("diagnosis")
        if main:
            lines.append(f"• {main}")
        fun = None
        # funnel from snap unit / locus
        locus = (snap.get("locus") or "").upper()
        if locus:
            lines.append(f"• Locus: {locus}")
        lines.append("• historical data unavailable — тренда CTR по срезам ещё нет.")
        return "\n".join(lines)
    return None


# --------------------------------------------------------------------------- #
# Turn handler
# --------------------------------------------------------------------------- #

def handle_dynamics_turn(
    text: str,
    *,
    ctx: DynamicsContext | None = None,
    points: Sequence[MetricPoint] | None = None,
    seller_data=None,
    product=None,
    finance_ctx=None,
    card_healthy: bool = False,
    depth: str | None = None,
    candidates: Sequence[Any] | None = None,
    current: Any = None,
    review_risks: Sequence[Any] | None = None,
    allow_synthetic_forecast: bool = False,
    points_by_article: dict[int, Sequence[MetricPoint]] | None = None,
) -> DynamicsTurnResult:
    """
    One chat turn. Reuses finance_planner.resolve_product for «мои белые Nike».
    Sticky: resolved article stays in DynamicsContext.
    """
    ctx = ctx or DynamicsContext()
    depth = depth or reply_depth(text)
    period = parse_period_hint(text) or ctx.period or "7d"
    ctx.period = period
    clarify: str | None = None

    # Product resolve (finance_planner) when candidates provided
    resolved_product = product
    if candidates is not None:
        try:
            from backend.ai.finance_planner import resolve_product as _resolve
            resolved, ambiguous, clarify_msg = _resolve(
                text, candidates, current=current,
            )
            if clarify_msg and ambiguous:
                clarify = clarify_msg
            elif resolved is not None:
                # ProductCandidate → use as sticky identity; real product may still be session product
                art = int(getattr(resolved, "article", 0) or 0)
                if art:
                    ctx.article = art
                    title = getattr(resolved, "title", None)
                    if title:
                        ctx.product_title = str(title)
                    # if session product matches, keep it; else keep sticky article for snapshot load
                    if product is not None and int(getattr(product, "article", 0) or 0) == art:
                        resolved_product = product
                    else:
                        resolved_product = product  # metrics still from session seller if same
        except Exception:
            pass

    if resolved_product is not None:
        art = getattr(resolved_product, "article", None)
        if art is not None:
            ctx.article = int(art)
        title = getattr(resolved_product, "title", None)
        if title:
            ctx.product_title = str(title)

    # Pick series for sticky / resolved article
    pts = list(points or [])
    if points_by_article and ctx.article is not None:
        alt = points_by_article.get(int(ctx.article))
        if alt is not None:
            pts = list(alt)

    if clarify and not pts:
        # ambiguous product — ask, don't invent dynamics
        stub = DynamicsDiagnosis(
            case=DynCase.INSUFFICIENT,
            title="Уточните товар",
            why=clarify,
            period=period,
            confidence=0.2,
            confidence_band="низкая",
            confidence_why="ambiguous product",
            action_class="INSUFFICIENT_DATA",
            n_points=0,
            historical_available=False,
            honesty=["Без артикула динамику не смешиваю."],
        )
        return DynamicsTurnResult(text=clarify, diagnosis=stub, ctx=ctx, clarify=clarify)

    diag = diagnose_dynamics(
        pts,
        period=period,
        seller_data=seller_data,
        product=resolved_product,
        finance_ctx=finance_ctx,
        card_healthy=card_healthy,
        review_risks=review_risks,
        prev_case=ctx.last_case,
        allow_synthetic_forecast=allow_synthetic_forecast,
    )
    ctx.last_case = diag.case.value
    ctx.last_summary = diag.title
    reply = format_dynamics_reply(
        diag,
        depth=depth,
        product_title=ctx.product_title,
    )
    if clarify:
        reply = clarify + "\n\n" + reply
    return DynamicsTurnResult(text=reply, diagnosis=diag, ctx=ctx, clarify=clarify)


# --------------------------------------------------------------------------- #
# Persistence helper (session / memory)
# --------------------------------------------------------------------------- #

def payload_has_measured_values(payload: dict[str, Any]) -> bool:
    """True if at least one non-None metric (None never stored as fact)."""
    keys = (
        "price", "rating", "feedbacks", "impressions", "views", "clicks", "ctr",
        "orders", "sales", "cvr", "revenue", "costs", "profit", "margin", "stock",
        "ad_spend", "cost", "returns",
    )
    return any(payload.get(k) is not None for k in keys)


async def persist_metric_snapshot(
    memory_store,
    user_id: int,
    article: int,
    *,
    seller_data=None,
    product=None,
    finance_ctx=None,
    marketplace: str = "wildberries",
    source: str = "session",
    min_interval_sec: float = 3600.0,
) -> int | None:
    """
    Save snapshot when seller/finance/card measured values available.
    No-op if empty. Refuses source=synthetic (never write synthetic into prod DB).
    """
    if memory_store is None:
        return None
    if str(source or "").lower().startswith("synthetic"):
        return None
    payload = build_snapshot_payload(
        seller_data=seller_data,
        product=product,
        finance_ctx=finance_ctx,
        source=source,
    )
    if not payload_has_measured_values(payload):
        return None
    try:
        from backend.ai.funnel_consistency import validate_funnel_fields
        res = validate_funnel_fields(
            impressions=payload.get("impressions"),
            clicks=payload.get("clicks"),
            orders=payload.get("orders"),
            ctr=payload.get("ctr"),
            cvr=payload.get("cvr"),
        )
        prov = payload.get("provenance") if isinstance(payload.get("provenance"), dict) else {}
        prov = dict(prov)
        prov["_funnel_quality"] = res.quality
        if res.reasons:
            prov["_funnel_quality_reasons"] = "; ".join(res.reasons[:3])
        payload["provenance"] = prov
        if not res.is_ok:
            payload["confidence"] = min(float(payload.get("confidence") or 0.3), 0.2)
    except Exception:
        pass
    # drop helper keys handled separately
    return await memory_store.save_metric_snapshot(
        user_id,
        article,
        marketplace,
        captured_at=payload.get("captured_at"),
        period=payload.get("period"),
        price=payload.get("price"),
        rating=payload.get("rating"),
        feedbacks=payload.get("feedbacks"),
        impressions=payload.get("impressions"),
        views=payload.get("views"),
        clicks=payload.get("clicks"),
        ctr=payload.get("ctr"),
        orders=payload.get("orders"),
        sales=payload.get("sales"),
        cvr=payload.get("cvr"),
        revenue=payload.get("revenue"),
        costs=payload.get("costs"),
        profit=payload.get("profit"),
        margin=payload.get("margin"),
        stock=payload.get("stock"),
        ad_spend=payload.get("ad_spend"),
        cost=payload.get("cost"),
        returns=payload.get("returns"),
        source=payload.get("source"),
        confidence=payload.get("confidence"),
        provenance=payload.get("provenance"),
        min_interval_sec=min_interval_sec,
    )
