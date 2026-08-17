"""
action_scheduler.py — background Action → Verification → Outcome automation.

Uses TimeService + ActionService + ActionObservationProvider + OutcomeFoundation.
Does not bind to Telegram handlers; optional notify callback for UX.
No ML. No new BrowserFetcher.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from backend.foundation.action_models import (
    ActionLifecyclePhase,
    ActionStatus,
    VerificationStatus,
)
from backend.foundation.action_observation import ActionObservationProvider, ObservationResult
from backend.foundation.action_service import ActionService
from backend.foundation.action_verification import VerificationResult
from backend.foundation.ml_readiness import build_learning_example
from backend.foundation.outcome_foundation import OutcomeFoundation, OutcomeRecord
from backend.foundation.time_service import TimeService, get_time_service

log = logging.getLogger("selleros.foundation.action_scheduler")

NotifyFn = Callable[[Any, str, dict[str, Any] | None], Awaitable[None]]
# (action, message, extras) — extras may include keyboard kind


@dataclass
class SchedulerTickResult:
    verified: list[str] = field(default_factory=list)
    seller_prompts: list[str] = field(default_factory=list)
    outcomes: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    browser_calls: int = 0
    notifications: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "verified": list(self.verified),
            "seller_prompts": list(self.seller_prompts),
            "outcomes": list(self.outcomes),
            "skipped": list(self.skipped),
            "errors": list(self.errors),
            "browser_calls": self.browser_calls,
            "notifications": self.notifications,
        }


def _metrics_from_fields(fields: dict[str, Any] | None) -> dict[str, Any]:
    out = {}
    for k in ("ctr", "cvr", "orders", "sales", "revenue", "clicks", "views", "price", "ad_spend"):
        if fields and fields.get(k) is not None:
            out[k] = fields[k]
    return out


def _metrics_from_snap(snap: Any) -> dict[str, Any]:
    if snap is None:
        return {}
    if isinstance(snap, dict):
        return _metrics_from_fields(snap)
    return _metrics_from_fields(
        {k: getattr(snap, k, None) for k in (
            "ctr", "cvr", "orders", "sales", "revenue", "clicks", "views", "price", "ad_spend",
        )}
    )


class ActionVerificationScheduler:
    """
    Periodic job:
      due_actions → observe → verify → notify
      due_outcome → snapshots → OutcomeFoundation → LearningExample(trainable=false)
    """

    def __init__(
        self,
        action_service: ActionService,
        *,
        observation: ActionObservationProvider | None = None,
        outcome_foundation: OutcomeFoundation | None = None,
        memory_store=None,
        time_service: TimeService | None = None,
        notify: NotifyFn | None = None,
        interval_sec: float = 300.0,
    ) -> None:
        self._actions = action_service
        self._store = memory_store or getattr(action_service, "_store", None)
        self._time = time_service or get_time_service()
        self._obs = observation or ActionObservationProvider(
            memory_store=self._store, time_service=self._time,
        )
        self._outcomes = outcome_foundation or OutcomeFoundation(
            action_service=action_service, time_service=self._time,
        )
        self._notify = notify
        self._interval = float(interval_sec)
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._notified_keys: set[str] = set()

    def start_background(self) -> asyncio.Task:
        if self._task and not self._task.done():
            return self._task
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="action_verification_scheduler")
        return self._task

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception as exc:
                log.exception("scheduler tick failed: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass

    async def tick(self, seller_id: int | None = None) -> SchedulerTickResult:
        result = SchedulerTickResult()
        # hydrate due from store when possible
        try:
            await self._actions.list_due_checks(seller_id)
        except Exception:
            pass

        for action in list(self._actions.due_actions(seller_id)):
            try:
                await self._process_verification(action.action_id, result)
            except Exception as exc:
                log.debug("verify due failed %s: %s", action.action_id, exc)
                result.errors.append(action.action_id)

        for action in list(self._actions.due_outcome_actions(seller_id)):
            try:
                await self._process_outcome(action.action_id, result)
            except Exception as exc:
                log.debug("outcome due failed %s: %s", action.action_id, exc)
                result.errors.append(action.action_id)

        return result

    async def _set_lifecycle(self, action_id: str, phase: ActionLifecyclePhase) -> None:
        a = await self._actions.get(action_id)
        if a is None:
            return
        a.metadata["lifecycle"] = phase.value
        a.metadata["lifecycle_at"] = self._time.timestamp()
        await self._actions._save(a)

    def _lifecycle_locked(self, action, phase: str, *, max_age: float = 3600.0) -> bool:
        """True if action is mid-flight in phase and lock is still fresh."""
        if (action.metadata or {}).get("lifecycle") != phase:
            return False
        at = (action.metadata or {}).get("lifecycle_at")
        if at is None:
            return False
        return (self._time.timestamp() - float(at)) < max_age

    async def _process_verification(self, action_id: str, result: SchedulerTickResult) -> None:
        action = await self._actions.get(action_id)
        if action is None:
            return
        if (action.metadata or {}).get("outcome_resolved"):
            result.skipped.append(action_id)
            return
        if self._lifecycle_locked(action, ActionLifecyclePhase.CHECKING.value):
            result.skipped.append(action_id)
            return
        if (action.metadata or {}).get("lifecycle") in (
            ActionLifecyclePhase.WAITING_OUTCOME.value,
            ActionLifecyclePhase.OUTCOME_CHECKING.value,
            ActionLifecyclePhase.OUTCOME_RECORDED.value,
        ):
            result.skipped.append(action_id)
            return

        await self._set_lifecycle(action_id, ActionLifecyclePhase.CHECKING)

        # Request fields from verification_spec when available
        fields = None
        if action.verification_spec and action.verification_spec.fields:
            fields = list(action.verification_spec.fields)

        obs = await self._obs.observe(
            action.seller_id,
            action.article,
            marketplace=action.marketplace or "wildberries",
            fields=fields,
        )
        if obs.browser_called:
            result.browser_calls += 1

        api_snap = obs.as_snapshot_view() if obs.verification_source == "api" else None
        parser_snap = obs.as_snapshot_view() if obs.verification_source == "parser" else None

        if not obs.available:
            action, vres = await self._actions.verify_action(action_id)
            if vres.needs_seller_confirmation:
                await self._notify_once(
                    action, "seller_prompt",
                    self._actions._verify.format_seller_prompt(action),
                    {"keyboard": "verification"},
                    result,
                )
                result.seller_prompts.append(action_id)
            else:
                result.skipped.append(action_id)
            await self._set_lifecycle(action_id, ActionLifecyclePhase.PENDING_CHECK)
            return

        kwargs = {
            "after_snapshot_id": obs.snapshot_id,
            "snapshot_source": obs.snapshot_source,
        }
        if api_snap is not None:
            payload = dict(api_snap.fields)
            if api_snap.timestamp is not None:
                payload["timestamp"] = api_snap.timestamp
            if api_snap.source:
                payload["source"] = api_snap.source
            action, vres = await self._actions.verify_action(
                action_id, api_snapshot=payload, **kwargs,
            )
        else:
            payload = dict(parser_snap.fields) if parser_snap else {}
            if parser_snap and parser_snap.timestamp is not None:
                payload["timestamp"] = parser_snap.timestamp
            action, vres = await self._actions.verify_action(
                action_id, parser_snapshot=payload, **kwargs,
            )

        # persist observation provenance on action
        action.metadata["last_observation"] = obs.to_dict()
        await self._actions._save(action)

        result.verified.append(action_id)
        if vres.status is VerificationStatus.NEEDS_REVIEW:
            await self._set_lifecycle(action_id, ActionLifecyclePhase.NEEDS_REVIEW)
        elif vres.status in (
            VerificationStatus.APPLIED,
            VerificationStatus.APPLIED_PARTIAL,
            VerificationStatus.SELLER_CONFIRMED,
        ):
            await self._set_lifecycle(action_id, ActionLifecyclePhase.WAITING_OUTCOME)
        else:
            await self._set_lifecycle(action_id, ActionLifecyclePhase.VERIFIED)
        await self._handle_verification_ux(action, vres, result)

    async def _handle_verification_ux(
        self,
        action,
        vres: VerificationResult,
        result: SchedulerTickResult,
    ) -> None:
        engine = self._actions._verify
        if vres.status is VerificationStatus.NEEDS_REVIEW:
            await self._notify_once(
                action, "needs_review",
                engine.format_result_message(action, vres),
                None,
                result,
            )
            return
        if vres.status is VerificationStatus.APPLIED:
            await self._notify_once(
                action, "auto_applied",
                engine.format_auto_applied(action, vres),
                None,
                result,
            )
            return
        if vres.status is VerificationStatus.APPLIED_PARTIAL:
            await self._notify_once(
                action, "auto_applied_partial",
                engine.format_auto_applied(action, vres),
                None,
                result,
            )
            return
        if vres.status is VerificationStatus.NOT_APPLIED:
            await self._notify_once(
                action, "not_applied",
                engine.format_result_message(action, vres),
                {"keyboard": "verification"},
                result,
            )
            return
        if vres.needs_seller_confirmation:
            await self._notify_once(
                action, "seller_prompt",
                engine.format_seller_prompt(action),
                {"keyboard": "verification"},
                result,
            )
            result.seller_prompts.append(action.action_id)

    async def _process_outcome(self, action_id: str, result: SchedulerTickResult) -> None:
        action = await self._actions.get(action_id)
        if action is None:
            return
        if (action.metadata or {}).get("outcome_resolved"):
            result.skipped.append(action_id)
            return
        if self._lifecycle_locked(action, ActionLifecyclePhase.OUTCOME_CHECKING.value):
            result.skipped.append(action_id)
            return

        await self._set_lifecycle(action_id, ActionLifecyclePhase.OUTCOME_CHECKING)

        before = _metrics_from_fields((action.metadata or {}).get("baseline_fields"))
        after: dict[str, Any] = {}
        after_sid = None
        n_points = 1

        if self._store is not None and hasattr(self._store, "list_metric_snapshots"):
            try:
                snaps = await self._store.list_metric_snapshots(
                    action.seller_id, action.article,
                    marketplace=action.marketplace or "wildberries",
                    limit=20,
                )
                n_points = len(snaps) if snaps else 1
                if snaps:
                    after = _metrics_from_snap(snaps[-1])
                    after_sid = getattr(snaps[-1], "id", None)
                    if action.baseline_snapshot_id is not None:
                        for s in snaps:
                            if getattr(s, "id", None) == action.baseline_snapshot_id:
                                before = before or _metrics_from_snap(s)
                                break
                    elif len(snaps) >= 2:
                        before = before or _metrics_from_snap(snaps[0])
            except Exception as exc:
                log.debug("outcome snaps skip: %s", exc)

        obs = await self._obs.observe(action.seller_id, action.article)
        if obs.browser_called:
            result.browser_calls += 1
        if obs.available and not after:
            after = _metrics_from_fields(obs.fields)

        if before and after:
            n_use = max(n_points, 2)
        else:
            n_use = 1

        rec = self._outcomes.evaluate(
            baseline_metrics=before,
            after_metrics=after,
            action_id=action_id,
            baseline_snapshot_id=action.baseline_snapshot_id,
            after_snapshot_id=after_sid,
            action_type=action.action_type.value,
            expected_effect=action.expected_effect,
            n_points=n_use,
            verification_status=action.verification_status.value,
            causal_attribution=action.causal_attribution.value,
            multiple_interventions=bool(
                action.action_group_id or (action.metadata or {}).get("multiple_interventions")
            ),
        )

        action = await self._actions.get(action_id)
        action.metadata["outcome_resolved"] = True
        action.metadata["outcome_record"] = rec.to_dict()
        action.metadata["phase"] = "outcome_resolved"
        action.metadata["lifecycle"] = ActionLifecyclePhase.OUTCOME_RECORDED.value
        action.after_snapshot_id = after_sid or action.after_snapshot_id
        await self._actions._save(action)
        if action.status is not ActionStatus.CHECKED:
            try:
                await self._actions.mark_checked(action_id)
            except Exception:
                pass

        action = await self._actions.get(action_id)
        ex = build_learning_example(
            action=action,
            baseline=before,
            after_metrics=after,
            outcome=rec,
            verification=(action.metadata or {}).get("verification"),
            evidence=list(action.verification_evidence or []),
            followup={
                "check_after": action.check_after,
                "outcome_after": action.outcome_after,
                "verification_source": action.verification_source,
                "snapshot_source": action.snapshot_source,
                "baseline_snapshot_id": action.baseline_snapshot_id,
                "after_snapshot_id": after_sid or action.after_snapshot_id,
                "outcome_reason": rec.actual_effect,
            },
        )
        action.metadata["learning_example"] = ex.to_dict()
        await self._actions._save(action)

        msg = self._outcomes.format_check_reply(rec)
        await self._notify_once(action, "outcome", msg, None, result)
        result.outcomes.append(action_id)

    async def _notify_once(
        self,
        action,
        kind: str,
        text: str,
        extras: dict[str, Any] | None,
        result: SchedulerTickResult,
    ) -> None:
        key = f"{action.action_id}:{kind}"
        if key in self._notified_keys:
            return
        # persist notify flag for process restarts via metadata
        flags = dict((action.metadata or {}).get("notify_sent") or {})
        if flags.get(kind):
            self._notified_keys.add(key)
            return
        if self._notify is not None:
            try:
                await self._notify(action, text, extras)
                result.notifications += 1
            except Exception as exc:
                log.debug("notify failed: %s", exc)
        flags[kind] = self._time.timestamp()
        action.metadata["notify_sent"] = flags
        await self._actions._save(action)
        self._notified_keys.add(key)
