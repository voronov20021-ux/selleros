"""
formula_engine.py — Formula Authority (deterministic).

LLM must NOT invent formulas. Missing → KNOWN / MISSING / NOT_INCLUDED.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class FormulaStatus(str, Enum):
    KNOWN = "KNOWN"
    MISSING = "MISSING"
    NOT_INCLUDED = "NOT_INCLUDED"


@dataclass
class FormulaSpec:
    formula_id: str
    name: str
    expression: str
    variables: list[str]
    units: str = ""
    required_inputs: list[str] = field(default_factory=list)
    optional_inputs: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    source: str = "argus_formula_authority_v1"
    version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "formula_id": self.formula_id,
            "name": self.name,
            "expression": self.expression,
            "variables": list(self.variables),
            "units": self.units,
            "required_inputs": list(self.required_inputs),
            "optional_inputs": list(self.optional_inputs),
            "limitations": list(self.limitations),
            "source": self.source,
            "version": self.version,
        }


@dataclass
class FormulaResult:
    name: str
    status: FormulaStatus
    value: float | None = None
    formula: str = ""
    formula_id: str | None = None
    variables: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    not_included: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "value": self.value,
            "formula": self.formula,
            "formula_id": self.formula_id,
            "variables": dict(self.variables),
            "missing": list(self.missing),
            "not_included": list(self.not_included),
            "notes": list(self.notes),
        }


def _f(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _build_registry() -> dict[str, FormulaSpec]:
    specs = [
        FormulaSpec("F_REVENUE", "Revenue", "Revenue = Price × Orders", ["price", "orders"], "₽",
                    required_inputs=["price", "orders"]),
        FormulaSpec("F_PROFIT", "Profit", "Profit = Revenue − KnownCosts", ["revenue", "known_costs"], "₽",
                    required_inputs=["revenue", "known_costs"],
                    limitations=["Только KnownCosts; неизвестное = MISSING/NOT_INCLUDED."]),
        FormulaSpec("F_MARGIN", "Margin%", "Margin% = Profit / Revenue × 100", ["profit", "revenue"], "%",
                    required_inputs=["profit", "revenue"],
                    limitations=["Маржа ≠ наценка."]),
        FormulaSpec("F_MARKUP", "Markup%", "Markup% = (SellingPrice − Cost) / Cost × 100",
                    ["selling_price", "cost"], "%",
                    required_inputs=["selling_price", "cost"],
                    limitations=["Наценка ≠ маржа."]),
        FormulaSpec("F_CTR", "CTR", "CTR = Clicks / Impressions × 100", ["clicks", "impressions"], "%",
                    required_inputs=["clicks", "impressions"],
                    optional_inputs=["ctr"],
                    limitations=["Нет универсального «хороший CTR=5%» без контекста."]),
        FormulaSpec("F_CVR", "CVR", "CVR = Orders / Clicks × 100", ["orders", "clicks"], "%",
                    required_inputs=["orders", "clicks"], optional_inputs=["cvr"]),
        FormulaSpec("F_CPC", "CPC", "CPC = AdSpend / Clicks", ["ad_spend", "clicks"], "₽",
                    required_inputs=["ad_spend", "clicks"]),
        FormulaSpec("F_CPM", "CPM", "CPM = AdSpend / Impressions × 1000", ["ad_spend", "impressions"], "₽",
                    required_inputs=["ad_spend", "impressions"]),
        FormulaSpec("F_CPA", "CPA", "CPA = AdSpend / Orders", ["ad_spend", "orders"], "₽",
                    required_inputs=["ad_spend", "orders"]),
        FormulaSpec("F_CAC", "CAC", "CAC = AcquisitionCost / NewCustomers",
                    ["acquisition_cost", "new_customers"], "₽",
                    required_inputs=["acquisition_cost", "new_customers"]),
        FormulaSpec("F_AOV", "AOV", "AOV = Revenue / Orders", ["revenue", "orders"], "₽",
                    required_inputs=["revenue", "orders"]),
        FormulaSpec("F_ROAS", "ROAS", "ROAS = Revenue / AdSpend", ["revenue", "ad_spend"], "x",
                    required_inputs=["revenue", "ad_spend"]),
        FormulaSpec("F_ROI", "ROI", "ROI = Profit / Investment × 100", ["profit", "investment"], "%",
                    required_inputs=["profit", "investment"]),
        FormulaSpec("F_CM", "ContributionMargin", "ContributionMargin = Revenue − VariableCosts",
                    ["revenue", "variable_costs"], "₽",
                    required_inputs=["revenue", "variable_costs"]),
        FormulaSpec("F_BE_UNITS", "BreakEvenUnits",
                    "BreakEvenUnits = FixedCosts / ContributionMarginPerUnit",
                    ["fixed_costs", "cm_per_unit"], "units",
                    required_inputs=["fixed_costs", "cm_per_unit"]),
        FormulaSpec("F_UNIT_PROFIT", "UnitProfit", "UnitProfit = SellingPrice − UnitCosts",
                    ["selling_price", "unit_costs"], "₽",
                    required_inputs=["selling_price", "unit_costs"]),
        FormulaSpec("F_MARGIN_UNIT", "MarginPerUnit", "MarginPerUnit = UnitProfit / SellingPrice",
                    ["unit_profit", "selling_price"], "ratio",
                    required_inputs=["unit_profit", "selling_price"],
                    limitations=["Маржа ≠ наценка."]),
        FormulaSpec("F_CPL", "CPL", "CPL = AdSpend / Leads", ["ad_spend", "leads"], "₽",
                    required_inputs=["ad_spend", "leads"]),
        FormulaSpec("F_COGS", "COGS", "COGS = PurchasePrice × Units", ["purchase_price", "units"], "₽",
                    required_inputs=["purchase_price", "units"]),
    ]
    return {s.formula_id: s for s in specs}


FORMULA_REGISTRY: dict[str, FormulaSpec] = _build_registry()


_EvalFn = Callable[[dict[str, Any]], FormulaResult]


class FormulaEngine:
    """Deterministic Formula Authority."""

    def __init__(self, registry: dict[str, FormulaSpec] | None = None) -> None:
        self.registry = dict(registry or FORMULA_REGISTRY)

    def specs(self) -> list[FormulaSpec]:
        return list(self.registry.values())

    def get_spec(self, formula_id: str) -> FormulaSpec | None:
        return self.registry.get(formula_id)

    def evaluate(self, formula_id: str, **inputs: Any) -> FormulaResult:
        spec = self.registry.get(formula_id)
        if spec is None:
            return FormulaResult(
                name=formula_id,
                status=FormulaStatus.MISSING,
                formula_id=formula_id,
                missing=["formula_id"],
                notes=["Формула не найдена в Formula Authority — не выдумываю."],
            )
        # alias map
        data = {k: _f(v) for k, v in inputs.items()}
        # given direct result shortcuts
        if formula_id == "F_CTR" and data.get("ctr") is not None:
            return FormulaResult("CTR", FormulaStatus.KNOWN, data["ctr"], spec.expression, formula_id,
                                {"ctr": data["ctr"]}, notes=["Прямое CTR, не вычислялось."])
        if formula_id == "F_CVR" and data.get("cvr") is not None:
            return FormulaResult("CVR", FormulaStatus.KNOWN, data["cvr"], spec.expression, formula_id,
                                {"cvr": data["cvr"]})

        missing = [k for k in spec.required_inputs if data.get(k) is None]
        if missing:
            return FormulaResult(
                name=spec.name, status=FormulaStatus.MISSING, formula=spec.expression,
                formula_id=formula_id, missing=missing, notes=list(spec.limitations),
            )

        try:
            value = self._compute(formula_id, data)
        except ZeroDivisionError:
            return FormulaResult(
                name=spec.name, status=FormulaStatus.MISSING, formula=spec.expression,
                formula_id=formula_id, missing=["divisor≠0"],
                notes=["Деление на ноль — результат не определён."] + list(spec.limitations),
            )
        except Exception as exc:
            return FormulaResult(
                name=spec.name, status=FormulaStatus.MISSING, formula=spec.expression,
                formula_id=formula_id, notes=[f"Ошибка расчёта: {exc}"],
            )

        if value is None:
            return FormulaResult(
                name=spec.name, status=FormulaStatus.MISSING, formula=spec.expression,
                formula_id=formula_id, missing=spec.required_inputs,
            )

        notes = list(spec.limitations)
        return FormulaResult(
            name=spec.name, status=FormulaStatus.KNOWN, value=float(value),
            formula=spec.expression, formula_id=formula_id,
            variables={k: data[k] for k in spec.required_inputs if data.get(k) is not None},
            notes=notes,
        )

    def _compute(self, fid: str, d: dict[str, float | None]) -> float | None:
        def req(*keys: str) -> tuple[float, ...]:
            vals = []
            for k in keys:
                v = d.get(k)
                if v is None:
                    raise KeyError(k)
                vals.append(float(v))
            return tuple(vals)

        if fid == "F_REVENUE":
            a, b = req("price", "orders"); return a * b
        if fid == "F_PROFIT":
            a, b = req("revenue", "known_costs"); return a - b
        if fid == "F_MARGIN":
            p, r = req("profit", "revenue")
            if r == 0:
                raise ZeroDivisionError
            return p / r * 100.0
        if fid == "F_MARKUP":
            sp, c = req("selling_price", "cost")
            if c == 0:
                raise ZeroDivisionError
            return (sp - c) / c * 100.0
        if fid == "F_CTR":
            c, i = req("clicks", "impressions")
            if i == 0:
                raise ZeroDivisionError
            return c / i * 100.0
        if fid == "F_CVR":
            o, c = req("orders", "clicks")
            if c == 0:
                raise ZeroDivisionError
            return o / c * 100.0
        if fid == "F_CPC":
            a, c = req("ad_spend", "clicks")
            if c == 0:
                raise ZeroDivisionError
            return a / c
        if fid == "F_CPM":
            a, i = req("ad_spend", "impressions")
            if i == 0:
                raise ZeroDivisionError
            return a / i * 1000.0
        if fid == "F_CPA":
            a, o = req("ad_spend", "orders")
            if o == 0:
                raise ZeroDivisionError
            return a / o
        if fid == "F_CAC":
            a, n = req("acquisition_cost", "new_customers")
            if n == 0:
                raise ZeroDivisionError
            return a / n
        if fid == "F_AOV":
            r, o = req("revenue", "orders")
            if o == 0:
                raise ZeroDivisionError
            return r / o
        if fid == "F_ROAS":
            r, a = req("revenue", "ad_spend")
            if a == 0:
                raise ZeroDivisionError
            return r / a
        if fid == "F_ROI":
            p, inv = req("profit", "investment")
            if inv == 0:
                raise ZeroDivisionError
            return p / inv * 100.0
        if fid == "F_CM":
            r, v = req("revenue", "variable_costs"); return r - v
        if fid == "F_BE_UNITS":
            f, cm = req("fixed_costs", "cm_per_unit")
            if cm == 0:
                raise ZeroDivisionError
            return f / cm
        if fid == "F_UNIT_PROFIT":
            sp, uc = req("selling_price", "unit_costs"); return sp - uc
        if fid == "F_MARGIN_UNIT":
            up, sp = req("unit_profit", "selling_price")
            if sp == 0:
                raise ZeroDivisionError
            return up / sp
        if fid == "F_CPL":
            a, l = req("ad_spend", "leads")
            if l == 0:
                raise ZeroDivisionError
            return a / l
        if fid == "F_COGS":
            p, u = req("purchase_price", "units"); return p * u
        return None

    # ── legacy helpers (delegate) ─────────────────────────────────────────

    def ctr(self, *, clicks=None, impressions=None, ctr=None) -> FormulaResult:
        return self.evaluate("F_CTR", clicks=clicks, impressions=impressions, ctr=ctr)

    def cvr(self, *, orders=None, clicks=None, cvr=None) -> FormulaResult:
        return self.evaluate("F_CVR", orders=orders, clicks=clicks, cvr=cvr)

    def profit(self, *, revenue=None, known_costs=None, costs: dict | None = None) -> FormulaResult:
        if known_costs is not None:
            return self.evaluate("F_PROFIT", revenue=revenue, known_costs=known_costs)
        if costs:
            included = {k: _f(v) for k, v in costs.items()}
            missing = [k for k, v in included.items() if v is None]
            known_sum = sum(v for v in included.values() if v is not None)
            if revenue is None:
                return FormulaResult("Profit", FormulaStatus.MISSING, formula="Profit = Revenue − KnownCosts",
                                    formula_id="F_PROFIT", missing=["revenue"])
            if missing:
                return FormulaResult(
                    "Profit", FormulaStatus.MISSING, formula="Profit = Revenue − KnownCosts",
                    formula_id="F_PROFIT",
                    variables={"revenue": float(revenue), "known_cost_parts": {k: v for k, v in included.items() if v is not None}},
                    missing=missing,
                    not_included=missing,
                    notes=["Часть расходов MISSING/NOT_INCLUDED — прибыль неполная, не выдумываю."],
                )
            return self.evaluate("F_PROFIT", revenue=revenue, known_costs=known_sum)
        return self.evaluate("F_PROFIT", revenue=revenue, known_costs=None)

    def margin(self, *, profit=None, revenue=None) -> FormulaResult:
        return self.evaluate("F_MARGIN", profit=profit, revenue=revenue)

    def markup(self, *, profit=None, cost=None, selling_price=None) -> FormulaResult:
        if selling_price is not None and cost is not None:
            return self.evaluate("F_MARKUP", selling_price=selling_price, cost=cost)
        # legacy: Markup = Profit/Cost*100 ≈ (SP-Cost)/Cost when profit=SP-Cost
        if profit is not None and cost is not None:
            c = _f(cost)
            p = _f(profit)
            if c is None or p is None or c == 0:
                return self.evaluate("F_MARKUP", selling_price=None, cost=cost)
            return FormulaResult(
                "Markup%", FormulaStatus.KNOWN, value=(p / c) * 100.0,
                formula="Markup% = Profit / Cost × 100 (legacy)",
                formula_id="F_MARKUP",
                variables={"profit": p, "cost": c},
                notes=["Наценка ≠ маржа."],
            )
        return self.evaluate("F_MARKUP", selling_price=selling_price, cost=cost)

    def cost_status(self, name: str, value: float | None) -> FormulaResult:
        if value is None:
            return FormulaResult(name, FormulaStatus.NOT_INCLUDED, missing=[name],
                                 not_included=[name],
                                 notes=[f"{name} не передан — не выдумываю."])
        return FormulaResult(name, FormulaStatus.KNOWN, value=float(value), variables={name: float(value)})

    def cost_stack(
        self,
        *,
        revenue: float | None = None,
        cogs: float | None = None,
        logistics: float | None = None,
        commission: float | None = None,
        storage: float | None = None,
        returns: float | None = None,
        advertising: float | None = None,
        taxes: float | None = None,
        penalties: float | None = None,
        other: float | None = None,
    ) -> dict[str, FormulaResult]:
        """Profit stack with explicit KNOWN/MISSING/NOT_INCLUDED per line."""
        lines = {
            "revenue": revenue,
            "COGS": cogs,
            "logistics": logistics,
            "commission": commission,
            "storage": storage,
            "returns": returns,
            "advertising": advertising,
            "taxes": taxes,
            "penalties": penalties,
            "other": other,
        }
        out: dict[str, FormulaResult] = {}
        known_costs = 0.0
        for name, val in lines.items():
            if name == "revenue":
                if val is None:
                    out["revenue"] = FormulaResult("revenue", FormulaStatus.MISSING, missing=["revenue"])
                else:
                    out["revenue"] = FormulaResult("revenue", FormulaStatus.KNOWN, float(val), variables={"revenue": float(val)})
                continue
            st = self.cost_status(name, _f(val) if val is not None else None)
            out[name] = st
            if st.status is FormulaStatus.KNOWN and st.value is not None:
                known_costs += st.value
        if out["revenue"].status is FormulaStatus.KNOWN:
            out["profit"] = self.evaluate(
                "F_PROFIT",
                revenue=out["revenue"].value,
                known_costs=known_costs,
            )
            out["profit"].notes.append(
                "Profit по сумме KNOWN costs; MISSING/NOT_INCLUDED строки не выдуманы."
            )
        else:
            out["profit"] = FormulaResult("Profit", FormulaStatus.MISSING, formula_id="F_PROFIT",
                                          missing=["revenue"])
        return out

    def from_finance_context(self, finance_ctx) -> dict[str, FormulaResult]:
        out: dict[str, FormulaResult] = {}
        try:
            from backend.ai.finance_planner import calculate
            calc = calculate(finance_ctx)
        except Exception as exc:
            out["finance"] = FormulaResult("finance", FormulaStatus.MISSING, notes=[str(exc)])
            return out

        def wrap(name: str, value, formula_id: str, expression: str):
            if value is None:
                out[name] = FormulaResult(name, FormulaStatus.MISSING, formula=expression,
                                          formula_id=formula_id, missing=[name])
            else:
                out[name] = FormulaResult(name, FormulaStatus.KNOWN, float(value), expression,
                                          formula_id, {name: float(value)})

        wrap("revenue", getattr(calc, "revenue", None), "F_REVENUE", "Revenue = Price × Orders")
        wrap("profit", getattr(calc, "profit", None) or getattr(calc, "net_profit", None),
             "F_PROFIT", "Profit = Revenue − KnownCosts")
        wrap("margin_pct", getattr(calc, "margin_pct", None), "F_MARGIN", "Margin% = Profit / Revenue × 100")
        wrap("markup_pct", getattr(calc, "markup_pct", None), "F_MARKUP", "Markup% = (SP − Cost) / Cost × 100")
        for key in ("commission", "ads", "returns", "tax", "storage"):
            val = getattr(finance_ctx, key, None) if finance_ctx is not None else None
            out[f"cost_{key}"] = self.cost_status(key, _f(val) if val is not None else None)
        return out

    def explain(self, result: FormulaResult) -> str:
        if result.status is FormulaStatus.KNOWN:
            v = result.value
            vs = f"{v:.4g}" if v is not None else "—"
            lines = [f"**{result.name}** = {vs}", f"Формула: `{result.formula}`"]
            if result.formula_id:
                lines.append(f"id: `{result.formula_id}`")
            lines.extend(result.notes)
            return "\n".join(lines)
        miss = ", ".join(result.missing) if result.missing else "—"
        lines = [
            f"**{result.name}** — данных недостаточно ({result.status.value}).",
            f"Формула: `{result.formula}`" if result.formula else "",
            f"Не хватает: {miss}",
            "Не выдумываю число.",
        ]
        lines.extend(result.notes)
        return "\n".join(x for x in lines if x)
