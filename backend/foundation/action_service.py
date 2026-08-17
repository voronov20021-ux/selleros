"""
action_service.py — structured seller action memory + due verification.

Persists in MemoryStore when available; otherwise in-process fallback.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from backend.foundation.action_models import (
    ActionStatus,
    ActionType,
    CausalAttribution,
    SellerAction,
    VerificationSpec,
    VerificationStatus,
    default_check_after_days,
    default_outcome_after_days,
)
from backend.foundation.action_verification import (
    ActionVerificationEngine,
    SellerConfirmIntent,
    SnapshotView,
    VerificationResult,
    normalize_card_fields,
)
from backend.foundation.time_service import TimeService, get_time_service

log = logging.getLogger("selleros.foundation.action_service")

_TRANSITIONS: dict[ActionStatus, frozenset[ActionStatus]] = {
    ActionStatus.PROPOSED: frozenset({ActionStatus.ACCEPTED, ActionStatus.CANCELLED}),
    ActionStatus.ACCEPTED: frozenset({ActionStatus.EXECUTED, ActionStatus.CANCELLED}),
    ActionStatus.EXECUTED: frozenset({ActionStatus.CHECK_PENDING, ActionStatus.CANCELLED}),
    ActionStatus.CHECK_PENDING: frozenset({ActionStatus.CHECKED, ActionStatus.CANCELLED}),
    ActionStatus.CHECKED: frozenset(),
    ActionStatus.CANCELLED: frozenset(),
}


class ActionService:
    """Seller action memory. Links baseline snapshot + check_after + verification."""

    def __init__(
        self,
        memory_store=None,
        time_service: TimeService | None = None,
        verification_engine: ActionVerificationEngine | None = None,
    ) -> None:
        self._store = memory_store
        self._time = time_service or get_time_service()
        self._verify = verification_engine or ActionVerificationEngine(self._time)
        self._mem: dict[str, SellerAction] = {}

    async def propose(
        self,
        seller_id: int,
        article: int,
        action_type: ActionType | str,
        recommendation: str,
        *,
        expected_effect: str | None = None,
        baseline_snapshot_id: int | None = None,
        baseline_fields: dict[str, Any] | None = None,
        diagnosis: str | None = None,
        check_after_days: float | None = None,
        outcome_after_days: float | None = None,
        metadata: dict[str, Any] | None = None,
        marketplace: str = "wildberries",
        verification_spec: VerificationSpec | dict | None = None,
        action_group_id: str | None = None,
        product=None,
    ) -> SellerAction:
        at = action_type if isinstance(action_type, ActionType) else ActionType(str(action_type))
        try:
            at = ActionType(at.value if isinstance(at, ActionType) else str(action_type))
        except ValueError:
            at = ActionType.OTHER
        check_days = (
            float(check_after_days)
            if check_after_days is not None
            else default_check_after_days(at)
        )
        outcome_days = (
            float(outcome_after_days)
            if outcome_after_days is not None
            else default_outcome_after_days(at)
        )
        sched = self._time.scheduled_followup(days=check_days)
        now = self._time.timestamp()
        meta = dict(metadata or {})
        meta["check_after_days"] = check_days
        meta["outcome_after_days"] = outcome_days
        fields = dict(baseline_fields or {})
        if product is not None and not fields:
            fields = normalize_card_fields(product)
        if fields:
            meta["baseline_fields"] = fields
            meta.setdefault("baseline_source", fields.get("source") or "analysis")
            meta.setdefault("baseline_quality", fields.get("quality") or "unknown")
        if action_group_id:
            meta["action_group_id"] = action_group_id
            meta["multiple_interventions"] = True

        if isinstance(verification_spec, dict):
            vs = VerificationSpec.from_dict(verification_spec)
        elif isinstance(verification_spec, VerificationSpec):
            vs = verification_spec
        else:
            vs = VerificationSpec.for_action_type(at)

        outcome_ts = self._time.check_after(days=outcome_days, from_ts=now)
        meta["outcome_after"] = outcome_ts

        action = SellerAction(
            action_id=str(uuid.uuid4()),
            seller_id=int(seller_id),
            article=int(article),
            action_type=at,
            recommendation=str(recommendation or "").strip(),
            status=ActionStatus.PROPOSED,
            created_at=now,
            baseline_snapshot_id=baseline_snapshot_id,
            expected_effect=expected_effect,
            check_after=sched["check_after"],
            outcome_after=outcome_ts,
            reminder_at=sched["reminder_at"],
            next_verification_at=sched["check_after"],
            diagnosis=diagnosis,
            marketplace=marketplace,
            verification_spec=vs,
            action_group_id=action_group_id,
            metadata=meta,
        )
        await self._save(action)
        return action

    async def propose_group(
        self,
        seller_id: int,
        article: int,
        items: list[dict[str, Any]],
        *,
        check_after_days: float = 7.0,
        product=None,
    ) -> list[SellerAction]:
        """Create multiple actions sharing action_group_id (mixed intervention)."""
        gid = str(uuid.uuid4())
        out = []
        for it in items:
            a = await self.propose(
                seller_id,
                article,
                it.get("action_type") or ActionType.OTHER,
                it.get("recommendation") or "",
                expected_effect=it.get("expected_effect"),
                baseline_snapshot_id=it.get("baseline_snapshot_id"),
                baseline_fields=it.get("baseline_fields"),
                diagnosis=it.get("diagnosis"),
                check_after_days=check_after_days,
                metadata=it.get("metadata"),
                verification_spec=it.get("verification_spec"),
                action_group_id=gid,
                product=product,
            )
            out.append(a)
        return out

    async def get(self, action_id: str) -> SellerAction | None:
        if action_id in self._mem:
            return self._mem[action_id]
        if self._store is not None and hasattr(self._store, "get_seller_action"):
            row = await self._store.get_seller_action(action_id)
            if row:
                a = SellerAction.from_dict(row)
                self._mem[a.action_id] = a
                return a
        return None

    async def list_for_product(
        self,
        seller_id: int,
        article: int,
        *,
        limit: int = 50,
    ) -> list[SellerAction]:
        out: list[SellerAction] = []
        if self._store is not None and hasattr(self._store, "list_seller_actions"):
            rows = await self._store.list_seller_actions(seller_id, article, limit=limit)
            for r in rows:
                a = SellerAction.from_dict(r)
                self._mem[a.action_id] = a
                out.append(a)
            return out
        for a in self._mem.values():
            if a.seller_id == seller_id and a.article == article:
                out.append(a)
        out.sort(key=lambda x: x.accepted_at or x.executed_at or x.created_at or 0, reverse=True)
        return out[:limit]

    def due_actions(self, seller_id: int | None = None) -> list[SellerAction]:
        """Synchronous view of in-memory due *application* checks (TimeService)."""
        now = self._time.timestamp()
        due = []
        for a in self._mem.values():
            if seller_id is not None and a.seller_id != seller_id:
                continue
            if a.status in (ActionStatus.CANCELLED, ActionStatus.CHECKED):
                continue
            # already verified application → outcome path owns the clock
            if a.verification_status in (
                VerificationStatus.APPLIED,
                VerificationStatus.APPLIED_PARTIAL,
                VerificationStatus.SELLER_CONFIRMED,
                VerificationStatus.NEEDS_REVIEW,
            ):
                continue
            if (a.metadata or {}).get("phase") == "outcome_pending":
                continue
            due_ts = a.next_verification_at or a.check_after
            if due_ts is not None and now >= float(due_ts):
                # skip if verified very recently (duplicate protection ~1h)
                if a.last_verification_at and (now - float(a.last_verification_at)) < 3600:
                    if a.verification_status not in (
                        VerificationStatus.UNKNOWN,
                        VerificationStatus.PENDING,
                        VerificationStatus.NOT_VERIFIABLE,
                        VerificationStatus.INCONCLUSIVE,
                    ):
                        continue
                due.append(a)
        return due

    async def list_due_checks(self, seller_id: int | None = None) -> list[SellerAction]:
        now = self._time.timestamp()
        items = list(self._mem.values())
        if self._store is not None and hasattr(self._store, "list_seller_actions_due"):
            rows = await self._store.list_seller_actions_due(seller_id, now)
            for r in rows:
                a = SellerAction.from_dict(r)
                self._mem[a.action_id] = a
            items = [self._mem[r["action_id"]] for r in rows if r.get("action_id") in self._mem]
            # also merge in-memory
            for a in list(self._mem.values()):
                if a not in items:
                    items.append(a)
        return [
            a for a in self.due_actions(seller_id)
        ]

    async def accept(self, action_id: str) -> SellerAction:
        return await self._transition(action_id, ActionStatus.ACCEPTED, stamp_field="accepted_at")

    async def mark_executed(
        self,
        action_id: str,
        *,
        executed_at: float | None = None,
        baseline_snapshot_id: int | None = None,
    ) -> SellerAction:
        a = await self._require(action_id)
        if baseline_snapshot_id is not None:
            a.baseline_snapshot_id = baseline_snapshot_id
        a.executed_at = executed_at if executed_at is not None else self._time.timestamp()
        if a.check_after is None or (a.metadata or {}).get("refresh_check_on_execute", True):
            days = float((a.metadata or {}).get("check_after_days") or default_check_after_days(a.action_type))
            sched = self._time.scheduled_followup(days=days, from_ts=a.executed_at)
            a.check_after = sched["check_after"]
            a.reminder_at = sched["reminder_at"]
            a.next_verification_at = sched["check_after"]
        out_days = float(
            (a.metadata or {}).get("outcome_after_days")
            or default_outcome_after_days(a.action_type)
        )
        a.outcome_after = self._time.check_after(days=out_days, from_ts=a.executed_at)
        a.metadata["outcome_after"] = a.outcome_after
        a.metadata["outcome_after_days"] = out_days
        a = await self._apply_status(a, ActionStatus.EXECUTED)
        return await self._apply_status(a, ActionStatus.CHECK_PENDING)

    async def mark_checked(self, action_id: str, *, outcome_id: str | None = None) -> SellerAction:
        a = await self._require(action_id)
        if outcome_id:
            a.outcome_id = outcome_id
        return await self._apply_status(a, ActionStatus.CHECKED)

    async def cancel(self, action_id: str, reason: str | None = None) -> SellerAction:
        a = await self._require(action_id)
        if reason:
            a.metadata["cancel_reason"] = reason
        return await self._apply_status(a, ActionStatus.CANCELLED)

    async def defer(self, action_id: str, *, days: float = 3.0) -> SellerAction:
        a = await self._require(action_id)
        sched = self._time.scheduled_followup(days=days)
        a.next_verification_at = sched["check_after"]
        a.check_after = sched["check_after"]
        a.verification_status = VerificationStatus.PENDING
        await self._save(a)
        return a

    def due_outcome_actions(self, seller_id: int | None = None) -> list[SellerAction]:
        """Actions with APPLIED/SELLER_CONFIRMED waiting for outcome_after."""
        now = self._time.timestamp()
        due = []
        for a in self._mem.values():
            if seller_id is not None and a.seller_id != seller_id:
                continue
            if a.status in (ActionStatus.CANCELLED, ActionStatus.CHECKED):
                continue
            if (a.metadata or {}).get("outcome_resolved"):
                continue
            if a.verification_status not in (
                VerificationStatus.APPLIED,
                VerificationStatus.APPLIED_PARTIAL,
                VerificationStatus.SELLER_CONFIRMED,
            ):
                continue
            oa = a.outcome_after or (a.metadata or {}).get("outcome_after")
            if oa is not None and now >= float(oa):
                due.append(a)
        return due

    async def schedule_outcome_window(self, action_id: str) -> SellerAction:
        """After application verified — keep CHECK_PENDING until outcome_after."""
        a = await self._require(action_id)
        days = float(
            (a.metadata or {}).get("outcome_after_days")
            or default_outcome_after_days(a.action_type)
        )
        base_ts = a.last_verification_at or a.executed_at or self._time.timestamp()
        a.outcome_after = self._time.check_after(days=days, from_ts=base_ts)
        a.metadata["outcome_after"] = a.outcome_after
        a.metadata["phase"] = "outcome_pending"
        a.metadata["outcome_pending"] = True
        a.metadata["lifecycle"] = "WAITING_OUTCOME"
        # stop re-running application verification every hour
        a.next_verification_at = a.outcome_after
        await self._save(a)
        return a

    async def record_verification(
        self,
        action_id: str,
        result: VerificationResult,
        *,
        after_snapshot_id: int | None = None,
        snapshot_source: str | None = None,
    ) -> SellerAction:
        a = await self._require(action_id)
        now = self._time.timestamp()
        # duplicate protection: same status within 1h
        if (
            a.last_verification_at
            and (now - float(a.last_verification_at)) < 3600
            and a.verification_status == result.status
            and not (result.details or {}).get("retry")
            and not (result.details or {}).get("conflict")
        ):
            return a
        a.last_verification_at = now
        a.verification_status = result.status
        a.verification_source = result.source
        a.verification_evidence = list(result.evidence)
        a.causal_attribution = result.causal_attribution
        if snapshot_source:
            a.snapshot_source = snapshot_source
        if after_snapshot_id is not None:
            a.after_snapshot_id = after_snapshot_id
        a.metadata["verification"] = result.to_dict()
        if snapshot_source:
            a.metadata["snapshot_source"] = snapshot_source
        if result.status is VerificationStatus.PENDING:
            days = float((a.metadata or {}).get("defer_days") or 3.0)
            a.next_verification_at = self._time.check_after(days=days)
        elif result.status in (
            VerificationStatus.APPLIED,
            VerificationStatus.APPLIED_PARTIAL,
            VerificationStatus.SELLER_CONFIRMED,
        ):
            await self._save(a)
            return await self.schedule_outcome_window(action_id)
        await self._save(a)
        return a

    async def verify_action(
        self,
        action_id: str,
        *,
        api_snapshot=None,
        parser_snapshot=None,
        baseline=None,
        seller_intent: SellerConfirmIntent | str | None = None,
        after_snapshot_id: int | None = None,
        snapshot_source: str | None = None,
        detect_conflict: bool = True,
    ) -> tuple[SellerAction, VerificationResult]:
        a = await self._require(action_id)
        base = baseline
        if base is None and (a.metadata or {}).get("baseline_fields"):
            base = (a.metadata or {}).get("baseline_fields")
        result = self._verify.verify(
            a,
            api_snapshot=api_snapshot,
            parser_snapshot=parser_snapshot,
            baseline=base,
            seller_intent=seller_intent,
            recommendation_at=a.accepted_at or a.created_at,
        )
        if detect_conflict and seller_intent is None:
            conflict = self._verify.resolve_conflict(a, result)
            if conflict is not None:
                result = conflict
        a = await self.record_verification(
            action_id,
            result,
            after_snapshot_id=after_snapshot_id,
            snapshot_source=snapshot_source,
        )
        return a, result

    async def _require(self, action_id: str) -> SellerAction:
        a = await self.get(action_id)
        if a is None:
            raise KeyError(f"SellerAction {action_id!r} not found")
        return a

    async def _transition(
        self,
        action_id: str,
        new_status: ActionStatus,
        *,
        stamp_field: str | None = None,
    ) -> SellerAction:
        a = await self._require(action_id)
        if stamp_field:
            setattr(a, stamp_field, self._time.timestamp())
        return await self._apply_status(a, new_status)

    async def _apply_status(self, a: SellerAction, new_status: ActionStatus) -> SellerAction:
        allowed = _TRANSITIONS.get(a.status, frozenset())
        if new_status not in allowed and a.status != new_status:
            if a.status is ActionStatus.EXECUTED and new_status is ActionStatus.CHECK_PENDING:
                pass
            elif a.status is ActionStatus.CHECK_PENDING and new_status is ActionStatus.CHECKED:
                pass
            elif a.status is new_status:
                pass
            else:
                raise ValueError(f"Invalid transition {a.status.value} → {new_status.value}")
        a.status = new_status
        await self._save(a)
        return a

    async def _save(self, a: SellerAction) -> None:
        self._mem[a.action_id] = a
        if self._store is not None and hasattr(self._store, "save_seller_action"):
            try:
                await self._store.save_seller_action(a.to_dict())
            except Exception as exc:
                log.debug("ActionService persist skip: %s", exc)


def dumps_meta(meta: dict | None) -> str:
    return json.dumps(meta or {}, ensure_ascii=False)
