"""
memory_chain.py — link Seller → Product → Snapshot → Diagnosis → Action → Follow-up → Outcome.

Does not duplicate SessionService / snapshots / OutcomeTracker — only composes IDs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MemoryChainLink:
    seller_id: int | None = None
    article: int | None = None
    marketplace: str = "wildberries"
    snapshot_id: int | None = None
    diagnosis: str | None = None
    action_id: str | None = None
    check_after: float | None = None
    outcome_id: str | None = None
    outcome_label: str | None = None
    finance_context_present: bool = False
    funnel_context_present: bool = False
    dynamics_context_present: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seller_id": self.seller_id,
            "article": self.article,
            "marketplace": self.marketplace,
            "snapshot_id": self.snapshot_id,
            "diagnosis": self.diagnosis,
            "action_id": self.action_id,
            "check_after": self.check_after,
            "outcome_id": self.outcome_id,
            "outcome_label": self.outcome_label,
            "finance_context_present": self.finance_context_present,
            "funnel_context_present": self.funnel_context_present,
            "dynamics_context_present": self.dynamics_context_present,
            "notes": list(self.notes),
        }


def build_memory_chain(
    *,
    seller_id: int | None = None,
    article: int | None = None,
    snapshot_id: int | None = None,
    diagnosis: str | None = None,
    action=None,
    outcome=None,
    session=None,
    user_id: int | None = None,
) -> MemoryChainLink:
    """Compose chain from existing session + action + outcome objects."""
    notes: list[str] = []
    fin = fun = dyn = False
    uid = user_id if user_id is not None else seller_id
    if session is not None and uid is not None:
        if hasattr(session, "get_finance_context"):
            fin = bool(session.get_finance_context(uid))
        if hasattr(session, "get_funnel_context"):
            fun = bool(session.get_funnel_context(uid))
        if hasattr(session, "get_dynamics_context"):
            dyn = bool(session.get_dynamics_context(uid))

    action_id = getattr(action, "action_id", None) if action is not None else None
    check_after = getattr(action, "check_after", None) if action is not None else None
    baseline = getattr(action, "baseline_snapshot_id", None) if action is not None else None
    art = article
    if art is None and action is not None:
        art = getattr(action, "article", None)
    if snapshot_id is None:
        snapshot_id = baseline

    outcome_id = None
    outcome_label = None
    if outcome is not None:
        outcome_id = getattr(outcome, "outcome_tracker_id", None) or getattr(outcome, "id", None)
        label = getattr(outcome, "outcome", None)
        outcome_label = label.value if hasattr(label, "value") else (str(label) if label else None)
        if getattr(outcome, "action_id", None):
            action_id = action_id or outcome.action_id

    if not snapshot_id:
        notes.append("snapshot_id отсутствует — цепочка неполная.")
    if action_id and not check_after:
        notes.append("action без check_after — follow-up не запланирован.")

    return MemoryChainLink(
        seller_id=seller_id,
        article=art,
        snapshot_id=snapshot_id,
        diagnosis=diagnosis or (getattr(action, "diagnosis", None) if action else None),
        action_id=action_id,
        check_after=check_after,
        outcome_id=str(outcome_id) if outcome_id else None,
        outcome_label=outcome_label,
        finance_context_present=fin,
        funnel_context_present=fun,
        dynamics_context_present=dyn,
        notes=notes,
    )
