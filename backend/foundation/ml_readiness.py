"""
ml_readiness.py — schema for future Learning Loop.

NO training. NO auto-learning. Collect structured history only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LearningExampleSchema:
    """
    Future ML training row shape.

    seller/product/action/baseline/verification/after/outcome/evidence/confidence
    """

    schema_version: str = "1.2"
    input_context: dict[str, Any] = field(default_factory=dict)
    facts: list[str] = field(default_factory=list)
    diagnosis: str | None = None
    action: dict[str, Any] = field(default_factory=dict)
    baseline: dict[str, Any] = field(default_factory=dict)
    followup: dict[str, Any] = field(default_factory=dict)
    verification: dict[str, Any] = field(default_factory=dict)
    after_metrics: dict[str, Any] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)
    outcome: str | None = None
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "input_context": dict(self.input_context),
            "facts": list(self.facts),
            "diagnosis": self.diagnosis,
            "action": dict(self.action),
            "baseline": dict(self.baseline),
            "followup": dict(self.followup),
            "verification": dict(self.verification),
            "after_metrics": dict(self.after_metrics),
            "evidence": list(self.evidence),
            "outcome": self.outcome,
            "confidence": self.confidence,
            "metadata": {
                **dict(self.metadata),
                "auto_learning": False,
                "trainable": False,
                "note": "Foundation only — do not train production reasoning on this yet.",
            },
            "trainable": False,
        }


def build_learning_example(
    *,
    input_context: dict[str, Any] | None = None,
    facts: list[str] | None = None,
    diagnosis: str | None = None,
    action=None,
    baseline: dict[str, Any] | None = None,
    followup: dict[str, Any] | None = None,
    after_metrics: dict[str, Any] | None = None,
    outcome=None,
    verification=None,
    evidence: list[str] | None = None,
    confidence: float = 0.0,
) -> LearningExampleSchema:
    action_dict: dict[str, Any] = {}
    if action is not None:
        action_dict = action.to_dict() if hasattr(action, "to_dict") else dict(action)

    outcome_label = None
    conf = confidence
    after = dict(after_metrics or {})
    base = dict(baseline or {})
    evid = list(evidence or [])
    ver: dict[str, Any] = {}
    if verification is not None:
        ver = verification.to_dict() if hasattr(verification, "to_dict") else dict(verification)
        evid = evid or list(ver.get("evidence") or [])
    if outcome is not None:
        if hasattr(outcome, "to_dict"):
            od = outcome.to_dict()
            outcome_label = od.get("outcome")
            conf = float(od.get("confidence") or conf)
            base = base or dict(od.get("baseline_metrics") or {})
            after = after or dict(od.get("after_metrics") or {})
        else:
            outcome_label = str(outcome)

    # convenience flat fields often used in Learning Loop dumps
    flat = {}
    if action_dict.get("action_type"):
        flat["action_type"] = action_dict.get("action_type")
    if ver.get("status"):
        flat["verification"] = ver.get("status")
        flat["verification_source"] = ver.get("source")
    for k in ("ctr", "cvr", "orders", "revenue"):
        if k in base:
            flat[f"before_{k}"] = base[k]
        if k in after:
            flat[f"after_{k}"] = after[k]

    return LearningExampleSchema(
        input_context=dict(input_context or {}),
        facts=list(facts or []),
        diagnosis=diagnosis,
        action={**action_dict, **flat},
        baseline=base,
        followup=dict(followup or {}),
        verification=ver,
        after_metrics=after,
        evidence=evid,
        outcome=outcome_label,
        confidence=conf,
        metadata={"flat": flat},
    )
