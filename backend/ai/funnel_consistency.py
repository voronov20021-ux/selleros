"""
funnel_consistency.py — deterministic funnel input validation.

Runs BEFORE diagnose_funnel / Dynamic Analytics causal trend.
Does not rewrite CTR_OK / CVR_OK / Cases A–D.

derived_clicks is math-only and is never persisted as observed clicks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


QUALITY_VALID = "VALID"
QUALITY_INVALID = "INVALID"
QUALITY_INCONSISTENT = "INCONSISTENT"

STATUS_CONSISTENT = "CONSISTENT"
STATUS_INCONSISTENT = "INCONSISTENT"
STATUS_INVALID = "INVALID"

# Absolute slack for integer funnel counts (rounding of derived clicks).
_COUNT_EPS = 0.51
# CTR provided vs clicks/impressions, percentage points.
_CTR_PP_EPS = 0.6


def _sf(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def derived_clicks(impressions: float | None, ctr: float | None) -> float | None:
    """impressions × CTR/100. Not an observed click count."""
    impr = _sf(impressions)
    rate = _sf(ctr)
    if impr is None or rate is None or impr < 0:
        return None
    return round(impr * rate / 100.0)


@dataclass
class FunnelConsistencyResult:
    status: str = STATUS_CONSISTENT
    quality: str = QUALITY_VALID
    reasons: list[str] = field(default_factory=list)
    impressions: float | None = None
    clicks: float | None = None
    derived_clicks: float | None = None
    orders: float | None = None
    ctr: float | None = None
    cvr: float | None = None
    human_message: str = ""
    check_line: str = ""

    @property
    def is_ok(self) -> bool:
        return self.status == STATUS_CONSISTENT and self.quality == QUALITY_VALID

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "quality": self.quality,
            "funnel_status": (
                STATUS_INCONSISTENT if not self.is_ok else STATUS_CONSISTENT
            ),
            "reasons": list(self.reasons),
            "impressions": self.impressions,
            "clicks": self.clicks,
            "derived_clicks": self.derived_clicks,
            "orders": self.orders,
            "ctr": self.ctr,
            "cvr": self.cvr,
            "human_message": self.human_message,
            "check_line": self.check_line,
            "observed_clicks": None,  # never persist derived as observed
        }


def _fail(
    *,
    status: str,
    quality: str,
    reasons: list[str],
    impressions: float | None,
    clicks: float | None,
    derived: float | None,
    orders: float | None,
    ctr: float | None,
    cvr: float | None,
) -> FunnelConsistencyResult:
    human, check = _humanize(
        impressions=impressions,
        derived=derived,
        clicks=clicks,
        orders=orders,
        ctr=ctr,
        reasons=reasons,
    )
    return FunnelConsistencyResult(
        status=status,
        quality=quality,
        reasons=reasons,
        impressions=impressions,
        clicks=clicks,
        derived_clicks=derived,
        orders=orders,
        ctr=ctr,
        cvr=cvr,
        human_message=human,
        check_line=check,
    )


def _humanize(
    *,
    impressions: float | None,
    derived: float | None,
    clicks: float | None,
    orders: float | None,
    ctr: float | None,
    reasons: list[str],
) -> tuple[str, str]:
    impr_s = f"{impressions:.0f}" if impressions is not None else "—"
    ctr_s = f"{ctr:g}%" if ctr is not None else "—"
    der_s = f"≈{derived:.0f}" if derived is not None else "—"
    ord_s = f"{orders:.0f}" if orders is not None else "—"
    if (
        impressions is not None
        and ctr is not None
        and derived is not None
        and orders is not None
        and derived + _COUNT_EPS < orders
    ):
        human = (
            "⚠️ Данные воронки противоречат друг другу.\n"
            f"При {impr_s} показах и CTR {ctr_s} получается около {derived:.0f} кликов,\n"
            f"но указано {ord_s} заказов.\n"
            "Проверьте клики, заказы и период."
        )
        check = (
            f"≈{derived:.0f} расчётных кликов не согласуются с {ord_s} заказами."
        )
        return human, check
    if (
        impressions is not None
        and clicks is not None
        and impressions + _COUNT_EPS < clicks
    ):
        human = (
            "⚠️ Данные воронки противоречат друг другу.\n"
            f"Показы {impr_s} меньше кликов {clicks:.0f}.\n"
            "Проверьте клики, заказы и период."
        )
        check = f"Показы {impr_s} не согласуются с кликами {clicks:.0f}."
        return human, check
    human = (
        "⚠️ Данные воронки противоречат друг другу.\n"
        + (reasons[0] if reasons else "Проверьте клики, заказы и период.")
    )
    if "проверьте" not in human.lower():
        human = human.rstrip(".") + ".\nПроверьте клики, заказы и период."
    check = reasons[0] if reasons else "Проверьте клики, заказы и период."
    return human, check


def validate_funnel_fields(
    *,
    impressions: Any = None,
    clicks: Any = None,
    orders: Any = None,
    ctr: Any = None,
    cvr: Any = None,
    same_period: bool = True,
) -> FunnelConsistencyResult:
    """
    Validate a single-period funnel vector.

    ``clicks`` must be observed clicks, not views-as-clicks.
    When clicks are absent, derived_clicks = impressions × CTR/100.
    """
    impr = _sf(impressions)
    clk = _sf(clicks)
    ordn = _sf(orders)
    ctr_v = _sf(ctr)
    cvr_v = _sf(cvr)
    derived = derived_clicks(impr, ctr_v) if clk is None else None
    effective_clicks = clk if clk is not None else derived

    reasons: list[str] = []
    invalid = False
    inconsistent = False

    for name, val in (
        ("показы", impr),
        ("клики", clk),
        ("заказы", ordn),
    ):
        if val is not None and val < 0:
            invalid = True
            reasons.append(f"{name} не могут быть отрицательными")

    if ctr_v is not None and not (0.0 <= ctr_v <= 100.0):
        invalid = True
        reasons.append(f"CTR {ctr_v:g}% вне диапазона 0..100")
    if cvr_v is not None and not (0.0 <= cvr_v <= 100.0):
        invalid = True
        reasons.append(f"CVR {cvr_v:g}% вне диапазона 0..100")

    if impr is not None and clk is not None and impr + _COUNT_EPS < clk:
        invalid = True
        reasons.append(
            f"показы {impr:.0f} < клики {clk:.0f} — невозможная CTR-связь"
        )

    if (
        same_period
        and effective_clicks is not None
        and ordn is not None
        and effective_clicks + _COUNT_EPS < ordn
    ):
        inconsistent = True
        src = "расчётные клики" if clk is None else "клики"
        reasons.append(
            f"{src} ≈{effective_clicks:.0f} < заказы {ordn:.0f} за тот же период"
        )

    if (
        impr is not None
        and clk is not None
        and impr > 0
        and ctr_v is not None
    ):
        actual_ctr = clk / impr * 100.0
        if abs(actual_ctr - ctr_v) > _CTR_PP_EPS:
            inconsistent = True
            reasons.append(
                f"CTR {ctr_v:g}% не согласуется с кликами {clk:.0f} / показами {impr:.0f}"
            )

    if invalid:
        return _fail(
            status=STATUS_INVALID,
            quality=QUALITY_INVALID,
            reasons=reasons,
            impressions=impr,
            clicks=clk,
            derived=derived if clk is None else derived_clicks(impr, ctr_v),
            orders=ordn,
            ctr=ctr_v,
            cvr=cvr_v,
        )
    if inconsistent:
        return _fail(
            status=STATUS_INCONSISTENT,
            quality=QUALITY_INCONSISTENT,
            reasons=reasons,
            impressions=impr,
            clicks=clk,
            derived=derived if clk is None else derived_clicks(impr, ctr_v),
            orders=ordn,
            ctr=ctr_v,
            cvr=cvr_v,
        )
    return FunnelConsistencyResult(
        status=STATUS_CONSISTENT,
        quality=QUALITY_VALID,
        impressions=impr,
        clicks=clk,
        derived_clicks=derived,
        orders=ordn,
        ctr=ctr_v,
        cvr=cvr_v,
    )


def validate_seller_funnel(seller_data) -> FunnelConsistencyResult:
    """Raw seller fields only. Does not treat views as observed clicks."""
    if seller_data is None:
        return FunnelConsistencyResult()
    return validate_funnel_fields(
        impressions=getattr(seller_data, "impressions", None),
        clicks=getattr(seller_data, "clicks", None),
        orders=getattr(seller_data, "orders", None),
        ctr=getattr(seller_data, "ctr", None),
        cvr=getattr(seller_data, "cvr", None),
        same_period=True,
    )


def validate_snapshot_metrics(snap: Any) -> FunnelConsistencyResult:
    """Validate one historical snapshot. Keep the row; mark quality."""
    def _g(name: str):
        if snap is None:
            return None
        if isinstance(snap, dict):
            return snap.get(name)
        return getattr(snap, name, None)

    return validate_funnel_fields(
        impressions=_g("impressions"),
        clicks=_g("clicks"),
        orders=_g("orders"),
        ctr=_g("ctr"),
        cvr=_g("cvr"),
        same_period=True,
    )
