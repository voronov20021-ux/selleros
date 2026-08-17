"""
outcome_foundation.py — structured Action → Follow-up → Outcome chain.

Wraps existing OutcomeTracker (intelligence/) — does NOT train ML.
Adds SUCCESS / NEUTRAL / FAILED / INCONCLUSIVE labels + snapshot linkage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from backend.foundation.time_service import TimeService, get_time_service
from backend.intelligence.outcomes import OutcomeDirection


class ActionOutcomeLabel(str, Enum):
    SUCCESS = "SUCCESS"
    NEUTRAL = "NEUTRAL"
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"
    NOT_EVALUABLE = "NOT_EVALUABLE"      # action not applied
    POSITIVE_SIGNAL = "POSITIVE_SIGNAL"  # cautious positive, not proven SUCCESS
    NEGATIVE_SIGNAL = "NEGATIVE_SIGNAL"  # cautious negative, not proven FAILED
    MIXED_INTERVENTION = "MIXED_INTERVENTION"


@dataclass
class MetricDelta:
    name: str
    before: float | None
    after: float | None
    delta: float | None
    pct_delta: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "before": self.before,
            "after": self.after,
            "delta": self.delta,
            "pct_delta": self.pct_delta,
        }


@dataclass
class OutcomeRecord:
    """
    Structured outcome for one action check.

    insufficient data → INCONCLUSIVE (never SUCCESS).
    """

    action_id: str | None
    outcome_tracker_id: str | None
    baseline_snapshot_id: int | None
    after_snapshot_id: int | None
    baseline_metrics: dict[str, Any] = field(default_factory=dict)
    after_metrics: dict[str, Any] = field(default_factory=dict)
    deltas: list[MetricDelta] = field(default_factory=list)
    period_start: float | None = None
    period_end: float | None = None
    action_type: str | None = None
    expected_effect: str | None = None
    actual_effect: str | None = None
    confidence: float = 0.0
    outcome: ActionOutcomeLabel = ActionOutcomeLabel.INCONCLUSIVE
    honesty: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "outcome_tracker_id": self.outcome_tracker_id,
            "baseline_snapshot_id": self.baseline_snapshot_id,
            "after_snapshot_id": self.after_snapshot_id,
            "baseline_metrics": dict(self.baseline_metrics),
            "after_metrics": dict(self.after_metrics),
            "deltas": [d.to_dict() for d in self.deltas],
            "period_start": self.period_start,
            "period_end": self.period_end,
            "action_type": self.action_type,
            "expected_effect": self.expected_effect,
            "actual_effect": self.actual_effect,
            "confidence": self.confidence,
            "outcome": self.outcome.value,
            "honesty": list(self.honesty),
            "metadata": dict(self.metadata),
        }


_LOWER_BETTER = frozenset({
    "returns", "return_rate", "cancel_rate", "cancellations",
    "ad_cost", "ad_spend", "cpc", "cpm", "bounce", "bounce_rate",
    "refunds", "complaints", "cost",
})


def _num(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def map_direction_to_label(
    direction: OutcomeDirection | str | None,
    *,
    comparisons: int,
    confidence: float,
    n_points: int = 2,
) -> ActionOutcomeLabel:
    """Map legacy OutcomeDirection → outcome labels. Never SUCCESS on thin data."""
    if comparisons <= 0 or confidence < 0.25:
        return ActionOutcomeLabel.INCONCLUSIVE
    d = direction.value if isinstance(direction, OutcomeDirection) else str(direction or "")
    d = d.lower()
    if d == "positive":
        # 2 points → cautious signal, not SUCCESS
        if n_points < 3:
            return ActionOutcomeLabel.POSITIVE_SIGNAL
        return ActionOutcomeLabel.SUCCESS
    if d == "negative":
        if n_points < 3:
            return ActionOutcomeLabel.NEGATIVE_SIGNAL
        return ActionOutcomeLabel.FAILED
    if d == "mixed":
        return ActionOutcomeLabel.NEUTRAL
    return ActionOutcomeLabel.INCONCLUSIVE


class OutcomeFoundation:
    """
    Chain:
      initial snapshot → diagnosis → recommendation → accepted → executed
      → check_after → next snapshot → before/after → outcome

    Reuses intelligence.OutcomeTracker when provided.
    Never marks SUCCESS without enough data.
    Never auto-trains ML.
    """

    def __init__(
        self,
        *,
        outcome_tracker=None,
        action_service=None,
        time_service: TimeService | None = None,
        min_comparisons: int = 1,
        significant_rel: float = 0.02,
    ) -> None:
        self._tracker = outcome_tracker
        self._actions = action_service
        self._time = time_service or get_time_service()
        self._min_comparisons = min_comparisons
        self._sig = significant_rel
        self._records: dict[str, OutcomeRecord] = {}

    def compare_metrics(
        self,
        before: dict[str, Any],
        after: dict[str, Any],
    ) -> tuple[list[MetricDelta], int, int, int]:
        deltas: list[MetricDelta] = []
        improved = worsened = 0
        for key, av in (after or {}).items():
            if key not in (before or {}):
                continue
            b = _num(before[key])
            a = _num(av)
            if b is None or a is None:
                continue
            delta = a - b
            pct = (delta / abs(b)) if abs(b) > 1e-9 else None
            deltas.append(MetricDelta(key, b, a, delta, pct))
            rel = (delta / max(abs(b), 1e-9))
            if key.lower() in _LOWER_BETTER:
                rel = -rel
            if abs(rel) < self._sig:
                continue
            if rel > 0:
                improved += 1
            else:
                worsened += 1
        return deltas, improved, worsened, improved + worsened

    def evaluate(
        self,
        *,
        baseline_metrics: dict[str, Any],
        after_metrics: dict[str, Any],
        action_id: str | None = None,
        outcome_tracker_id: str | None = None,
        baseline_snapshot_id: int | None = None,
        after_snapshot_id: int | None = None,
        period_start: float | None = None,
        period_end: float | None = None,
        action_type: str | None = None,
        expected_effect: str | None = None,
        use_tracker_analyze: bool = True,
        n_points: int = 2,
        verification_status: str | None = None,
        causal_attribution: str | None = None,
        multiple_interventions: bool = False,
    ) -> OutcomeRecord:
        honesty: list[str] = []
        before = dict(baseline_metrics or {})
        after = dict(after_metrics or {})

        # APPLICATION vs OUTCOME: not applied → NOT_EVALUABLE (not FAILED)
        vstat = (verification_status or "").upper()
        if vstat in ("NOT_APPLIED", "PENDING"):
            honesty.append("Действие не применено — outcome не оценивается (не FAILED).")
            rec = OutcomeRecord(
                action_id=action_id,
                outcome_tracker_id=outcome_tracker_id,
                baseline_snapshot_id=baseline_snapshot_id,
                after_snapshot_id=after_snapshot_id,
                baseline_metrics=before,
                after_metrics=after,
                period_start=period_start,
                period_end=period_end,
                action_type=action_type,
                expected_effect=expected_effect,
                actual_effect="not_applied",
                confidence=0.0,
                outcome=ActionOutcomeLabel.NOT_EVALUABLE,
                honesty=honesty,
                metadata={"verification_status": vstat, "applied_ne_success": True},
            )
            if action_id:
                self._records[action_id] = rec
            return rec

        if multiple_interventions or (causal_attribution or "").upper() == "MIXED_INTERVENTION":
            honesty.append(
                "Несколько вмешательств одновременно — не приписываю весь эффект одному action."
            )

        if not before or not after:
            honesty.append("Недостаточно данных: нет baseline и/или after метрик.")
            rec = OutcomeRecord(
                action_id=action_id,
                outcome_tracker_id=outcome_tracker_id,
                baseline_snapshot_id=baseline_snapshot_id,
                after_snapshot_id=after_snapshot_id,
                baseline_metrics=before,
                after_metrics=after,
                period_start=period_start,
                period_end=period_end,
                action_type=action_type,
                expected_effect=expected_effect,
                actual_effect="данных недостаточно",
                confidence=0.0,
                outcome=ActionOutcomeLabel.INCONCLUSIVE,
                honesty=honesty,
            )
            if action_id:
                self._records[action_id] = rec
            return rec

        if n_points <= 1:
            honesty.append("0–1 snapshot: NO OUTCOME.")
            rec = OutcomeRecord(
                action_id=action_id,
                outcome_tracker_id=outcome_tracker_id,
                baseline_snapshot_id=baseline_snapshot_id,
                after_snapshot_id=after_snapshot_id,
                baseline_metrics=before,
                after_metrics=after,
                period_start=period_start,
                period_end=period_end,
                action_type=action_type,
                expected_effect=expected_effect,
                actual_effect="insufficient_history",
                confidence=0.0,
                outcome=ActionOutcomeLabel.INCONCLUSIVE,
                honesty=honesty,
            )
            if action_id:
                self._records[action_id] = rec
            return rec

        deltas, improved, worsened, comparisons = self.compare_metrics(before, after)

        direction = OutcomeDirection.UNKNOWN
        conf = 0.0
        if use_tracker_analyze and self._tracker is not None:
            analysis = self._tracker.analyze_result(before, after)
            direction = analysis.outcome_direction
            conf = float(analysis.confidence)
            comparisons = max(comparisons, int(analysis.comparisons))
            improved = analysis.improved
            worsened = analysis.worsened
        else:
            if comparisons == 0:
                direction = OutcomeDirection.UNKNOWN
                conf = 0.15
            elif improved > worsened:
                direction = OutcomeDirection.POSITIVE
                conf = min(0.9, 0.4 + 0.1 * comparisons)
            elif worsened > improved:
                direction = OutcomeDirection.NEGATIVE
                conf = min(0.9, 0.4 + 0.1 * comparisons)
            else:
                direction = OutcomeDirection.MIXED
                conf = 0.35

        if multiple_interventions or (causal_attribution or "").upper() == "MIXED_INTERVENTION":
            label = ActionOutcomeLabel.MIXED_INTERVENTION
            honesty.append("Причинность по одному action не доказана.")
        elif (causal_attribution or "").upper() == "UNKNOWN":
            label = ActionOutcomeLabel.INCONCLUSIVE
            honesty.append("Изменение могло быть до рекомендации — causal attribution UNKNOWN.")
        else:
            label = map_direction_to_label(
                direction, comparisons=comparisons, confidence=conf, n_points=n_points,
            )

        if comparisons < self._min_comparisons and label not in (
            ActionOutcomeLabel.MIXED_INTERVENTION,
            ActionOutcomeLabel.NOT_EVALUABLE,
        ):
            label = ActionOutcomeLabel.INCONCLUSIVE
            honesty.append("Слишком мало сопоставимых метрик — не считаю успехом.")
        if label is ActionOutcomeLabel.INCONCLUSIVE:
            honesty.append("Результат INCONCLUSIVE: данных недостаточно для SUCCESS/FAILED.")
        if label is ActionOutcomeLabel.POSITIVE_SIGNAL:
            honesty.append("APPLIED ≠ SUCCESS. Положительный сигнал, причинность не доказана.")
        if vstat in ("APPLIED", "SELLER_CONFIRMED"):
            honesty.append("Verification=APPLIED/SELLER_CONFIRMED не означает SUCCESS.")

        actual = (
            f"improved={improved}, worsened={worsened}, comparisons={comparisons}, n_points={n_points}"
        )
        rec = OutcomeRecord(
            action_id=action_id,
            outcome_tracker_id=outcome_tracker_id,
            baseline_snapshot_id=baseline_snapshot_id,
            after_snapshot_id=after_snapshot_id,
            baseline_metrics=before,
            after_metrics=after,
            deltas=deltas,
            period_start=period_start or None,
            period_end=period_end or self._time.timestamp(),
            action_type=action_type,
            expected_effect=expected_effect,
            actual_effect=actual,
            confidence=conf,
            outcome=label,
            honesty=honesty,
            metadata={
                "verification_status": vstat or None,
                "causal_attribution": causal_attribution,
                "multiple_interventions": multiple_interventions,
                "n_points": n_points,
            },
        )
        if action_id:
            self._records[action_id] = rec
        return rec

    async def check_action(
        self,
        action_id: str,
        after_metrics: dict[str, Any],
        *,
        after_snapshot_id: int | None = None,
        baseline_metrics: dict[str, Any] | None = None,
    ) -> OutcomeRecord:
        """Evaluate due action using ActionService baseline + after metrics."""
        if self._actions is None:
            raise RuntimeError("ActionService required for check_action")
        action = await self._actions.get(action_id)
        if action is None:
            raise KeyError(action_id)

        before = dict(baseline_metrics or {})
        if not before and action.baseline_snapshot_id is not None:
            # caller should pass metrics; snapshot fetch is store-side
            honesty_note = "baseline_snapshot_id set but metrics not passed"
        else:
            honesty_note = None

        period_start = action.executed_at or action.accepted_at
        vstat = getattr(action, "verification_status", None)
        vstat_s = vstat.value if hasattr(vstat, "value") else (str(vstat) if vstat else None)
        causal = getattr(action, "causal_attribution", None)
        causal_s = causal.value if hasattr(causal, "value") else (str(causal) if causal else None)
        multi = bool((action.metadata or {}).get("multiple_interventions") or action.action_group_id)
        rec = self.evaluate(
            baseline_metrics=before,
            after_metrics=after_metrics,
            action_id=action_id,
            outcome_tracker_id=action.outcome_id,
            baseline_snapshot_id=action.baseline_snapshot_id,
            after_snapshot_id=after_snapshot_id,
            period_start=period_start,
            period_end=self._time.timestamp(),
            action_type=action.action_type.value,
            expected_effect=action.expected_effect,
            n_points=2 if before and after_metrics else 1,
            verification_status=vstat_s,
            causal_attribution=causal_s,
            multiple_interventions=multi,
        )
        if honesty_note:
            rec.honesty.append(honesty_note)

        # optional: push into existing OutcomeTracker
        if self._tracker is not None and action.outcome_id:
            try:
                await self._tracker.record_result(
                    action.outcome_id,
                    rec.baseline_metrics,
                    rec.after_metrics,
                    period_start or self._time.timestamp(),
                    rec.period_end or self._time.timestamp(),
                )
            except Exception:
                rec.honesty.append("OutcomeTracker.record_result skipped")

        await self._actions.mark_checked(action_id, outcome_id=action.outcome_id)
        return rec

    def get_record(self, action_id: str) -> OutcomeRecord | None:
        return self._records.get(action_id)

    def format_check_reply(self, rec: OutcomeRecord) -> str:
        lines = ["### 📋 ПРОВЕРКА РЕЗУЛЬТАТА", ""]
        lines.append(f"**Итог:** {rec.outcome.value}")
        lines.append(f"**Уверенность:** {rec.confidence:.0%}")
        if rec.expected_effect:
            lines.append(f"**Ожидали:** {rec.expected_effect}")
        if rec.actual_effect:
            lines.append(f"**Факт:** {rec.actual_effect}")
        if rec.deltas:
            lines.append("")
            lines.append("| Метрика | Было | Стало | Δ |")
            lines.append("|---|---:|---:|---:|")
            for d in rec.deltas[:8]:
                lines.append(
                    f"| {d.name} | {_fmt(d.before)} | {_fmt(d.after)} | {_fmt(d.delta)} |"
                )
        for h in rec.honesty[:3]:
            lines.append(f"_{h}_")
        if rec.outcome is ActionOutcomeLabel.INCONCLUSIVE:
            lines.append("")
            lines.append("Не считаю действие успешным — данных недостаточно.")
        return "\n".join(lines)


def _fmt(v: float | None) -> str:
    if v is None:
        return "—"
    if abs(v) >= 100:
        return f"{v:.0f}"
    return f"{v:.2f}"
