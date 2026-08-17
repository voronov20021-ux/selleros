"""action_models.py — structured seller action memory + verification specs."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ActionType(str, Enum):
    CHANGE_MAIN_PHOTO = "CHANGE_MAIN_PHOTO"
    CHANGE_GALLERY_PHOTO = "CHANGE_GALLERY_PHOTO"
    CHANGE_PRICE = "CHANGE_PRICE"
    CHANGE_TITLE = "CHANGE_TITLE"
    CHANGE_DESCRIPTION = "CHANGE_DESCRIPTION"
    CHANGE_CHARACTERISTICS = "CHANGE_CHARACTERISTICS"
    START_AD = "START_AD"
    STOP_AD = "STOP_AD"
    CHANGE_AD_BUDGET = "CHANGE_AD_BUDGET"
    CHANGE_STOCK = "CHANGE_STOCK"
    REPLENISH_STOCK = "REPLENISH_STOCK"
    CHANGE_PROCUREMENT = "CHANGE_PROCUREMENT"
    CHANGE_SUPPLIER = "CHANGE_SUPPLIER"
    CHANGE_PURCHASE_PRICE = "CHANGE_PURCHASE_PRICE"
    CHANGE_LOGISTICS = "CHANGE_LOGISTICS"
    CUSTOM = "CUSTOM"
    OTHER = "OTHER"


class ActionStatus(str, Enum):
    PROPOSED = "PROPOSED"
    ACCEPTED = "ACCEPTED"
    EXECUTED = "EXECUTED"
    CHECK_PENDING = "CHECK_PENDING"
    CHECKED = "CHECKED"
    CANCELLED = "CANCELLED"


class VerificationStatus(str, Enum):
    APPLIED = "APPLIED"
    APPLIED_PARTIAL = "APPLIED_PARTIAL"  # some target fields applied, some not
    NOT_APPLIED = "NOT_APPLIED"
    NOT_VERIFIABLE = "NOT_VERIFIABLE"
    INCONCLUSIVE = "INCONCLUSIVE"  # missing/partial snapshot — not NOT_APPLIED
    SELLER_CONFIRMED = "SELLER_CONFIRMED"
    NEEDS_REVIEW = "NEEDS_REVIEW"  # seller YES vs later objective unchanged
    UNKNOWN = "UNKNOWN"
    PENDING = "PENDING"


class ActionLifecyclePhase(str, Enum):
    """Scheduler idempotency phases (stored in metadata['lifecycle'])."""

    PENDING_CHECK = "PENDING_CHECK"
    CHECKING = "CHECKING"
    VERIFIED = "VERIFIED"
    WAITING_OUTCOME = "WAITING_OUTCOME"
    OUTCOME_CHECKING = "OUTCOME_CHECKING"
    OUTCOME_RECORDED = "OUTCOME_RECORDED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


# Configurable application-check vs outcome windows (days).
DEFAULT_CHECK_AFTER_DAYS: dict[ActionType, float] = {
    ActionType.CHANGE_MAIN_PHOTO: 1.0,
    ActionType.CHANGE_GALLERY_PHOTO: 1.0,
    ActionType.CHANGE_PRICE: 1.0,
    ActionType.CHANGE_TITLE: 1.0,
    ActionType.CHANGE_DESCRIPTION: 1.0,
    ActionType.CHANGE_CHARACTERISTICS: 1.0,
    ActionType.CHANGE_AD_BUDGET: 1.0,
    ActionType.CHANGE_STOCK: 1.0,
    ActionType.REPLENISH_STOCK: 1.0,
}
DEFAULT_OUTCOME_AFTER_DAYS: dict[ActionType, float] = {
    ActionType.CHANGE_MAIN_PHOTO: 7.0,
    ActionType.CHANGE_GALLERY_PHOTO: 7.0,
    ActionType.CHANGE_PRICE: 7.0,
    ActionType.CHANGE_TITLE: 7.0,
    ActionType.CHANGE_DESCRIPTION: 7.0,
    ActionType.CHANGE_CHARACTERISTICS: 7.0,
    ActionType.CHANGE_AD_BUDGET: 7.0,
    ActionType.CHANGE_STOCK: 7.0,
    ActionType.REPLENISH_STOCK: 7.0,
}
FALLBACK_CHECK_AFTER_DAYS = 1.0
FALLBACK_OUTCOME_AFTER_DAYS = 7.0


def default_check_after_days(action_type: ActionType | str) -> float:
    try:
        at = action_type if isinstance(action_type, ActionType) else ActionType(str(action_type))
    except ValueError:
        return FALLBACK_CHECK_AFTER_DAYS
    return float(DEFAULT_CHECK_AFTER_DAYS.get(at, FALLBACK_CHECK_AFTER_DAYS))


def default_outcome_after_days(action_type: ActionType | str) -> float:
    try:
        at = action_type if isinstance(action_type, ActionType) else ActionType(str(action_type))
    except ValueError:
        return FALLBACK_OUTCOME_AFTER_DAYS
    return float(DEFAULT_OUTCOME_AFTER_DAYS.get(at, FALLBACK_OUTCOME_AFTER_DAYS))


class CausalAttribution(str, Enum):
    NOT_ASSESSED = "NOT_ASSESSED"  # verification not run / causality not judged yet
    ELIGIBLE = "ELIGIBLE"          # change after recommendation
    UNKNOWN = "UNKNOWN"            # change before recommendation / unclear
    NOT_EVALUABLE = "NOT_EVALUABLE"  # not applied
    MIXED_INTERVENTION = "MIXED_INTERVENTION"


# Default verification fields per action type
DEFAULT_VERIFICATION_FIELDS: dict[ActionType, list[str]] = {
    ActionType.CHANGE_MAIN_PHOTO: ["main_photo_hash"],
    ActionType.CHANGE_GALLERY_PHOTO: ["photo_set_hash"],
    ActionType.CHANGE_PRICE: ["price"],
    ActionType.CHANGE_TITLE: ["title_hash"],
    ActionType.CHANGE_DESCRIPTION: ["description_hash"],
    ActionType.CHANGE_CHARACTERISTICS: ["characteristics_hash"],
    ActionType.CHANGE_AD_BUDGET: ["ad_spend"],
    ActionType.CHANGE_STOCK: ["stock"],
    ActionType.REPLENISH_STOCK: ["stock"],
    ActionType.CHANGE_PURCHASE_PRICE: ["cost"],
    ActionType.CHANGE_LOGISTICS: ["logistics"],
    ActionType.START_AD: ["ad_spend"],
    ActionType.STOP_AD: ["ad_spend"],
    ActionType.CHANGE_PROCUREMENT: ["cost"],
    ActionType.CHANGE_SUPPLIER: ["cost"],
    ActionType.CUSTOM: [],
    ActionType.OTHER: [],
}


@dataclass
class VerificationSpec:
    """What to check to prove the action was applied (not outcome)."""

    method: list[str] = field(default_factory=lambda: ["API", "PARSER", "SELLER_CONFIRMATION"])
    fields: list[str] = field(default_factory=list)
    expected_change: str = "DIFFERENT"  # DIFFERENT | ANY | EQUAL
    # Optional exact targets, e.g. {"price": 4190} with expected_change=EQUAL
    expected_values: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": list(self.method),
            "fields": list(self.fields),
            "expected_change": self.expected_change,
            "expected_values": dict(self.expected_values or {}),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "VerificationSpec":
        d = d or {}
        return cls(
            method=list(d.get("method") or ["API", "PARSER", "SELLER_CONFIRMATION"]),
            fields=list(d.get("fields") or []),
            expected_change=str(d.get("expected_change") or "DIFFERENT"),
            expected_values=dict(d.get("expected_values") or {}),
        )

    @classmethod
    def for_action_type(cls, action_type: ActionType | str) -> "VerificationSpec":
        at = action_type if isinstance(action_type, ActionType) else ActionType(str(action_type))
        return cls(fields=list(DEFAULT_VERIFICATION_FIELDS.get(at, [])))


@dataclass
class SellerAction:
    """
    Structured seller action linked to product + baseline snapshot.

    Verification (applied?) is separate from Outcome (did it help?).
    """

    action_id: str
    seller_id: int
    article: int
    action_type: ActionType
    recommendation: str
    status: ActionStatus = ActionStatus.PROPOSED
    created_at: float | None = None
    accepted_at: float | None = None
    executed_at: float | None = None
    baseline_snapshot_id: int | None = None
    after_snapshot_id: int | None = None
    expected_effect: str | None = None
    check_after: float | None = None
    outcome_after: float | None = None  # when to evaluate metrics (≠ check_after)
    reminder_at: float | None = None
    last_verification_at: float | None = None
    next_verification_at: float | None = None
    verification_status: VerificationStatus = VerificationStatus.UNKNOWN
    verification_source: str | None = None  # api | parser | seller_confirmation | unavailable
    snapshot_source: str | None = None  # e.g. WB_SELLER_API | PUBLIC_CACHE | METRIC_SNAPSHOT
    verification_evidence: list[str] = field(default_factory=list)
    causal_attribution: CausalAttribution = CausalAttribution.NOT_ASSESSED
    action_group_id: str | None = None
    outcome_id: str | None = None
    diagnosis: str | None = None
    marketplace: str = "wildberries"
    verification_spec: VerificationSpec | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.verification_spec is None:
            self.verification_spec = VerificationSpec.for_action_type(self.action_type)
        if self.next_verification_at is None and self.check_after is not None:
            self.next_verification_at = self.check_after
        if self.outcome_after is None and (self.metadata or {}).get("outcome_after") is not None:
            self.outcome_after = self.metadata.get("outcome_after")

    @property
    def product_id(self) -> int:
        return self.article

    def to_dict(self) -> dict[str, Any]:
        meta = dict(self.metadata or {})
        # keep verification_spec also nested in metadata for older store rows
        vs = self.verification_spec.to_dict() if self.verification_spec else None
        if vs:
            meta.setdefault("verification_spec", vs)
        if self.action_group_id:
            meta.setdefault("action_group_id", self.action_group_id)
        if self.created_at is not None:
            meta.setdefault("created_at", self.created_at)
        if self.after_snapshot_id is not None:
            meta.setdefault("after_snapshot_id", self.after_snapshot_id)
        if self.last_verification_at is not None:
            meta.setdefault("last_verification_at", self.last_verification_at)
        if self.next_verification_at is not None:
            meta.setdefault("next_verification_at", self.next_verification_at)
        if self.verification_status:
            meta["verification_status"] = self.verification_status.value
        if self.verification_source:
            meta["verification_source"] = self.verification_source
        if self.verification_evidence:
            meta["verification_evidence"] = list(self.verification_evidence)
        if self.causal_attribution:
            meta["causal_attribution"] = self.causal_attribution.value
        if self.outcome_after is not None:
            meta["outcome_after"] = self.outcome_after
        if self.snapshot_source:
            meta["snapshot_source"] = self.snapshot_source
        return {
            "action_id": self.action_id,
            "seller_id": self.seller_id,
            "article": self.article,
            "product_id": self.article,
            "action_type": self.action_type.value,
            "recommendation": self.recommendation,
            "status": self.status.value,
            "created_at": self.created_at,
            "accepted_at": self.accepted_at,
            "executed_at": self.executed_at,
            "baseline_snapshot_id": self.baseline_snapshot_id,
            "after_snapshot_id": self.after_snapshot_id,
            "expected_effect": self.expected_effect,
            "check_after": self.check_after,
            "outcome_after": self.outcome_after,
            "reminder_at": self.reminder_at,
            "last_verification_at": self.last_verification_at,
            "next_verification_at": self.next_verification_at,
            "verification_status": self.verification_status.value,
            "verification_source": self.verification_source,
            "snapshot_source": self.snapshot_source,
            "verification_evidence": list(self.verification_evidence),
            "causal_attribution": self.causal_attribution.value,
            "action_group_id": self.action_group_id,
            "outcome_id": self.outcome_id,
            "diagnosis": self.diagnosis,
            "marketplace": self.marketplace,
            "verification_spec": vs,
            "metadata": meta,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SellerAction":
        meta = dict(d.get("metadata") or {})
        vs_raw = d.get("verification_spec") or meta.get("verification_spec")
        vs = VerificationSpec.from_dict(vs_raw) if vs_raw else None
        at_raw = str(d.get("action_type") or "OTHER")
        try:
            at = ActionType(at_raw)
        except ValueError:
            at = ActionType.OTHER
        vstat = str(
            d.get("verification_status")
            or meta.get("verification_status")
            or VerificationStatus.UNKNOWN.value
        )
        try:
            verification_status = VerificationStatus(vstat)
        except ValueError:
            verification_status = VerificationStatus.UNKNOWN
        causal = str(
            d.get("causal_attribution")
            or meta.get("causal_attribution")
            or CausalAttribution.UNKNOWN.value
        )
        try:
            causal_attribution = CausalAttribution(causal)
        except ValueError:
            causal_attribution = CausalAttribution.NOT_ASSESSED
        return cls(
            action_id=str(d["action_id"]),
            seller_id=int(d["seller_id"]),
            article=int(d.get("article") or d.get("product_id") or 0),
            action_type=at,
            recommendation=str(d.get("recommendation") or ""),
            status=ActionStatus(str(d.get("status") or "PROPOSED")),
            created_at=d.get("created_at") or meta.get("created_at"),
            accepted_at=d.get("accepted_at"),
            executed_at=d.get("executed_at"),
            baseline_snapshot_id=d.get("baseline_snapshot_id"),
            after_snapshot_id=d.get("after_snapshot_id") or meta.get("after_snapshot_id"),
            expected_effect=d.get("expected_effect"),
            check_after=d.get("check_after"),
            outcome_after=d.get("outcome_after") or meta.get("outcome_after"),
            reminder_at=d.get("reminder_at"),
            last_verification_at=d.get("last_verification_at") or meta.get("last_verification_at"),
            next_verification_at=d.get("next_verification_at") or meta.get("next_verification_at"),
            verification_status=verification_status,
            verification_source=d.get("verification_source") or meta.get("verification_source"),
            snapshot_source=d.get("snapshot_source") or meta.get("snapshot_source"),
            verification_evidence=list(
                d.get("verification_evidence") or meta.get("verification_evidence") or []
            ),
            causal_attribution=causal_attribution,
            action_group_id=d.get("action_group_id") or meta.get("action_group_id"),
            outcome_id=d.get("outcome_id"),
            diagnosis=d.get("diagnosis"),
            marketplace=str(d.get("marketplace") or "wildberries"),
            verification_spec=vs,
            metadata=meta,
        )
