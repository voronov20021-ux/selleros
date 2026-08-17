"""
action_bridge.py — light bridge: Advisor recommendation → ActionService.propose.

Does not rewrite Advisor. No Browser. No ML.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from backend.ai.advisor import auto_action_eligible
from backend.foundation.action_models import ActionType
from backend.foundation.action_service import ActionService
from backend.foundation.action_verification import normalize_card_fields

log = logging.getLogger("selleros.foundation.action_bridge")

_PHOTO = re.compile(r"\b(фото|главн\w*\s+фото|photo|изображен)", re.I)
_GALLERY = re.compile(r"\b(галере|доп\w*\s+фото|gallery)", re.I)
_PRICE = re.compile(r"\b(цен[аыуе]|price|скидк)", re.I)
_TITLE = re.compile(r"\b(назван|title|заголов)", re.I)
_DESC = re.compile(r"\b(описан|description)", re.I)
_CHARS = re.compile(r"\b(характеристик|атрибут)", re.I)
_ADS = re.compile(r"\b(реклам|бюджет|CPC|ставк)", re.I)
_STOCK = re.compile(r"\b(остат|stock|пополн)", re.I)


_CHECK_FIRST = re.compile(
    r"ничего не трогай|не менять карточку автоматически|"
    r"первый шаг — получить ctr|собрать ctr|сначала ctr",
    re.I,
)


def infer_action_type(text: str) -> ActionType:
    t = str(text or "")
    if _GALLERY.search(t):
        return ActionType.CHANGE_GALLERY_PHOTO
    if _PHOTO.search(t):
        return ActionType.CHANGE_MAIN_PHOTO
    if _PRICE.search(t):
        return ActionType.CHANGE_PRICE
    if _TITLE.search(t):
        return ActionType.CHANGE_TITLE
    if _DESC.search(t):
        return ActionType.CHANGE_DESCRIPTION
    if _CHARS.search(t):
        return ActionType.CHANGE_CHARACTERISTICS
    if _ADS.search(t):
        return ActionType.CHANGE_AD_BUDGET
    if _STOCK.search(t):
        return ActionType.CHANGE_STOCK
    return ActionType.CUSTOM


def _item_layer(item: Any) -> str:
    layer = getattr(item, "layer", None)
    if layer is None:
        return ""
    return getattr(layer, "value", None) or str(layer)


def _pick_primary_text(advisor_plan) -> tuple[str, str | None]:
    """Return (recommendation_text, diagnosis). Skip IDEA/CHECK-first lines."""
    if advisor_plan is None:
        return "", None
    diagnosis = None
    for attr in ("main_problem", "main_verdict", "bottleneck"):
        item = getattr(advisor_plan, attr, None)
        if item is None:
            continue
        txt = item if isinstance(item, str) else getattr(item, "text", None)
        if txt:
            diagnosis = str(txt)[:240]
            break

    def _usable(text: str, item: Any = None) -> bool:
        if not text or not str(text).strip():
            return False
        if _CHECK_FIRST.search(str(text)):
            return False
        if item is not None:
            if _item_layer(item).upper() == "IDEA":
                return False
            meta = getattr(item, "metadata", None) or {}
            if str(meta.get("action_class") or "").upper() in ("IDEA", "CHECK", "MONITOR"):
                return False
        return True

    do_first = getattr(advisor_plan, "do_first", None)
    if isinstance(do_first, str) and _usable(do_first):
        return do_first.strip(), diagnosis
    for bucket in ("fixes", "priority"):
        items = getattr(advisor_plan, bucket, None)
        if not items:
            continue
        if isinstance(items, str):
            if _usable(items):
                return items.strip(), diagnosis
            continue
        first = items[0]
        text = first if isinstance(first, str) else getattr(first, "text", "")
        if _usable(str(text), first if not isinstance(first, str) else None):
            return str(text).strip(), diagnosis
    return "", diagnosis


async def propose_primary_from_plan(
    *,
    action_service: ActionService,
    seller_id: int,
    article: int,
    advisor_plan,
    product=None,
    baseline_snapshot_id: int | None = None,
    check_after_days: float = 7.0,
) -> Any | None:
    """
    Create one PROPOSED Action from the primary advisor fix.
    Returns SellerAction or None if nothing actionable.
    """
    if advisor_plan is not None and not auto_action_eligible(advisor_plan):
        log.info(
            "action_bridge: skip auto-action article=%s kind=%s",
            article,
            getattr(advisor_plan, "main_problem_kind", ""),
        )
        return None
    rec, diagnosis = _pick_primary_text(advisor_plan)
    if not rec:
        return None
    at = infer_action_type(rec)
    kind = str(getattr(advisor_plan, "main_problem_kind", "") or "")
    if at == ActionType.CHANGE_DESCRIPTION and kind != "problem":
        log.info("action_bridge: skip CHANGE_DESCRIPTION without confirmed diagnosis")
        return None
    fields = normalize_card_fields(product)
    try:
        action = await action_service.propose(
            seller_id,
            article,
            at,
            rec[:500],
            expected_effect=None,
            baseline_snapshot_id=baseline_snapshot_id,
            baseline_fields=fields or None,
            diagnosis=diagnosis,
            check_after_days=check_after_days,
            product=product,
            metadata={"origin": "advisor_plan", "bridge": "action_bridge"},
        )
        return action
    except Exception as exc:
        log.debug("propose_primary_from_plan skip: %s", exc)
        return None
