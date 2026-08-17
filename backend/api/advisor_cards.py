"""Thin AdvisorPlan → Mini App first-screen cards.

Does not recompute funnel, scores, or Advisor math. Reads fields already
produced by ``build_advisor_plan`` / ``AdvisorPlan.format_first_screen``.
"""

from __future__ import annotations

from typing import Any

from backend.ai.advisor import AdvisorPlan, ClaimLayer
from backend.api.miniapp_copy import human_funnel_status


def _item_text(it: Any) -> str:
    if it is None:
        return ""
    if isinstance(it, str):
        return it.strip()
    return str(getattr(it, "text", "") or "").strip()


def _layer_value(it: Any) -> str:
    layer = getattr(it, "layer", None)
    if layer is None:
        return ""
    return layer.value if hasattr(layer, "value") else str(layer)


def first_screen_cards(plan: AdvisorPlan | None) -> dict[str, Any]:
    """Structured first-screen blocks for the Mini App (not Telegram dump)."""
    if plan is None:
        return {
            "verdict": "Данных для разбора пока мало.",
            "verdict_kind": "",
            "figures": [],
            "do": [],
            "dont": [],
            "check": [],
            "confidence": "",
            "priority_tier": "",
            "funnel_consistency": None,
            "idea_only": [],
            "details": {},
        }

    snap = plan.diagnosis_snapshot()
    meta = plan.metadata if isinstance(plan.metadata, dict) else {}
    funnel = meta.get("funnel_consistency") if isinstance(meta.get("funnel_consistency"), dict) else None

    verdict = (
        (snap.get("main_problem") or "").strip()
        or (snap.get("main_verdict") or "").strip()
        or (plan.diagnosis or "").strip()
        or "Данных для жёсткого вывода пока мало."
    )

    figures: list[dict[str, Any]] = []
    if funnel:
        for key, label in (
            ("impressions", "Показы"),
            ("clicks", "Клики"),
            ("derived_clicks", "Расчётные клики"),
            ("orders", "Заказы"),
            ("ctr", "CTR"),
            ("cvr", "CVR"),
        ):
            val = funnel.get(key)
            if val is not None:
                figures.append({"label": label, "value": val, "source": "funnel_consistency"})
        status = funnel.get("status") or funnel.get("funnel_status")
        if status:
            figures.append({
                "label": "Воронка",
                "value": human_funnel_status(status),
                "source": "funnel",
            })

    for f in list(plan.facts or [])[:8]:
        text = _item_text(f)
        if not text:
            continue
        low = text.lower()
        if any(
            k in low
            for k in ("рейтинг", "цена", "отзыв", "фото", "ctr", "cvr", "score", "артикул")
        ):
            figures.append({"label": "Факт", "value": text, "source": "fact"})
    figures = figures[:8]

    do: list[str] = []
    if (plan.do_first or "").strip():
        do.append(plan.do_first.strip())
    for it in list(plan.fixes or [])[:5]:
        t = _item_text(it)
        if t and t not in do:
            do.append(t)

    dont: list[str] = []
    if (plan.leave_alone or "").strip():
        dont.append(plan.leave_alone.strip())
    for it in list(plan.not_recommended or [])[:5]:
        t = _item_text(it)
        if t and t not in dont:
            dont.append(t)

    check: list[str] = []
    if funnel and funnel.get("check_line"):
        check.append(str(funnel["check_line"]))
    if (plan.data_needed or "").strip():
        check.append(plan.data_needed.strip())
    for it in list(plan.to_verify or [])[:5]:
        t = _item_text(it)
        if t and t not in check:
            check.append(t)

    idea_only = [
        _item_text(p)
        for p in list(plan.problems or [])
        if _layer_value(p) == ClaimLayer.IDEA.value and _item_text(p)
    ]

    telegram_text = ""
    try:
        telegram_text = plan.format_first_screen()
    except Exception:
        telegram_text = ""

    details = {
        "known": [_item_text(x) for x in (plan.known or plan.facts or [])[:8] if _item_text(x)],
        "assumed": [_item_text(x) for x in (plan.assumed or [])[:6] if _item_text(x)],
        "why": list(plan.why_points or [])[:6],
        "verdict_full": plan.main_verdict or "",
        "telegram_first_screen": telegram_text,
        "unit_economics": snap.get("unit_economics"),
        "market_compare": snap.get("market_compare"),
        "dynamic_analytics": snap.get("dynamic_analytics"),
        "photos_analyzed": bool(plan.photos_analyzed),
    }

    return {
        "verdict": verdict,
        "verdict_kind": plan.main_problem_kind or "",
        "card_healthy": bool(meta.get("card_healthy")),
        "figures": figures,
        "do": do[:6],
        "dont": dont[:6],
        "check": check[:6],
        "confidence": plan.confidence_label or "",
        "confidence_why": plan.confidence_why or "",
        "priority_tier": plan.priority_tier or "",
        "priority_why": plan.priority_why or "",
        "funnel_consistency": funnel,
        "idea_only": idea_only[:6],
        "details": details,
    }
