"""
action_verification.py — deterministic Action APPLICATION verification.

APPLIED ≠ SUCCESS. Verification ≠ Outcome.
Never marks APPLIED just because check_after elapsed.
Never calls Browser — consumes provided snapshots / seller confirmation only.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from backend.foundation.action_models import (
    ActionType,
    CausalAttribution,
    SellerAction,
    VerificationSpec,
    VerificationStatus,
)
from backend.foundation.time_service import TimeService, get_time_service


class SellerConfirmIntent(str, Enum):
    YES_APPLIED = "YES_APPLIED"
    NO_NOT_APPLIED = "NO_NOT_APPLIED"
    NOT_YET = "NOT_YET"
    CHECK_AGAIN = "CHECK_AGAIN"


@dataclass
class SnapshotView:
    """Normalized observable state for verification (not full DB row)."""

    product_id: int | None = None
    timestamp: float | None = None
    source: str | None = None
    source_timestamp: float | None = None
    quality: str | None = None  # high | medium | low | unknown
    fields: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str) -> Any:
        return self.fields.get(key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "timestamp": self.timestamp,
            "source": self.source,
            "source_timestamp": self.source_timestamp,
            "quality": self.quality,
            "fields": dict(self.fields),
        }

    @classmethod
    def from_mapping(
        cls,
        data: dict[str, Any] | None,
        *,
        product_id: int | None = None,
        source: str | None = None,
        timestamp: float | None = None,
        quality: str | None = None,
    ) -> "SnapshotView":
        data = dict(data or {})
        meta_keys = {
            "product_id", "article", "timestamp", "captured_at", "source",
            "source_timestamp", "quality", "id", "snapshot_id",
        }
        fields = {k: v for k, v in data.items() if k not in meta_keys and v is not None}
        pid = product_id
        if pid is None:
            pid = data.get("product_id") or data.get("article")
        ts = timestamp
        if ts is None and data.get("timestamp") is not None:
            ts = float(data["timestamp"])
        if ts is None and data.get("captured_at") is not None:
            ts = float(data["captured_at"])
        src_ts = None
        if data.get("source_timestamp") is not None:
            src_ts = float(data["source_timestamp"])
        return cls(
            product_id=int(pid) if pid is not None else None,
            timestamp=float(ts) if ts is not None else None,
            source=source or data.get("source"),
            source_timestamp=src_ts,
            quality=quality or data.get("quality") or "unknown",
            fields=fields,
        )


@dataclass
class VerificationResult:
    status: VerificationStatus
    source: str | None = None  # api | parser | seller_confirmation | none
    evidence: list[str] = field(default_factory=list)
    target_applied: bool | None = None
    other_changes: list[str] = field(default_factory=list)
    causal_attribution: CausalAttribution = CausalAttribution.NOT_ASSESSED
    needs_seller_confirmation: bool = False
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "source": self.source,
            "evidence": list(self.evidence),
            "target_applied": self.target_applied,
            "other_changes": list(self.other_changes),
            "causal_attribution": self.causal_attribution.value,
            "needs_seller_confirmation": self.needs_seller_confirmation,
            "message": self.message,
            "details": dict(self.details),
        }


def hash_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
    else:
        raw = str(value).encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()[:16]


def normalize_card_fields(product=None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build observable hash fields from a product-like object without Browser."""
    out: dict[str, Any] = dict(extra or {})
    if product is None:
        return out
    price = getattr(product, "price", None)
    if price is not None:
        out.setdefault("price", price)
    title = getattr(product, "title", None)
    if title:
        out.setdefault("title_hash", hash_value(title))
    desc = getattr(product, "description", None)
    if desc:
        out.setdefault("description_hash", hash_value(desc))
    photos = getattr(product, "photos", None)
    if photos is not None:
        if isinstance(photos, (list, tuple)) and photos:
            first = photos[0]
            url = first if isinstance(first, str) else getattr(first, "url", None) or str(first)
            out.setdefault("main_photo_hash", hash_value(url))
            joined = "|".join(
                (p if isinstance(p, str) else str(getattr(p, "url", p))) for p in photos[:20]
            )
            out.setdefault("photo_set_hash", hash_value(joined))
        elif isinstance(photos, int):
            out.setdefault("photo_count", photos)
    chars = getattr(product, "characteristics", None)
    if chars:
        out.setdefault("characteristics_hash", hash_value(str(sorted(chars.items()) if isinstance(chars, dict) else chars)))
    stock = getattr(product, "stock", None)
    if stock is not None:
        out.setdefault("stock", stock)
    rating = getattr(product, "rating", None)
    if rating is not None:
        out.setdefault("rating", rating)
    fb = getattr(product, "feedbacks", None)
    if fb is not None:
        out.setdefault("feedbacks", fb)
    return out


def _field_changed(before: Any, after: Any, expected: str = "DIFFERENT") -> bool | None:
    if before is None and after is None:
        return None  # not verifiable
    if before is None or after is None:
        return None
    different = before != after
    if expected == "DIFFERENT":
        return different
    if expected == "EQUAL":
        return not different
    return different  # ANY → treat as change detection


def _field_matches_expected(current: Any, expected: Any) -> bool | None:
    """Deterministic EQUAL-to-target. None if either side missing."""
    if current is None or expected is None:
        return None
    try:
        if isinstance(expected, (int, float)) and not isinstance(expected, bool):
            return abs(float(current) - float(expected)) < 1e-9
    except (TypeError, ValueError):
        pass
    return current == expected


def characteristics_delta(before: Any, after: Any) -> str:
    """CHANGED | UNCHANGED | UNKNOWN — deterministic."""
    if before is None or after is None:
        return "UNKNOWN"
    return "CHANGED" if before != after else "UNCHANGED"


class ActionVerificationEngine:
    """
    Verify whether the *recommended action* was applied.

    Order: API snapshot → parser/analysis snapshot → seller confirmation.
    Does not evaluate SUCCESS/FAILED outcomes.
    """

    def __init__(self, time_service: TimeService | None = None) -> None:
        self._time = time_service or get_time_service()

    def compare_snapshots(
        self,
        action: SellerAction,
        baseline: SnapshotView | dict[str, Any],
        current: SnapshotView | dict[str, Any] | None,
        *,
        source: str = "parser",
        recommendation_at: float | None = None,
    ) -> VerificationResult:
        base = baseline if isinstance(baseline, SnapshotView) else SnapshotView.from_mapping(baseline)
        if current is None:
            return VerificationResult(
                status=VerificationStatus.INCONCLUSIVE,
                source=source,
                needs_seller_confirmation=True,
                message="Текущий snapshot недоступен — автоматически подтвердить изменение нельзя.",
            )
        cur = current if isinstance(current, SnapshotView) else SnapshotView.from_mapping(current)

        spec = action.verification_spec or VerificationSpec.for_action_type(action.action_type)
        fields = list(spec.fields or [])
        expected_values = dict(spec.expected_values or {})
        if not fields and expected_values:
            fields = list(expected_values.keys())
        if not fields:
            return VerificationResult(
                status=VerificationStatus.INCONCLUSIVE,
                source=source,
                needs_seller_confirmation=True,
                message="Для этого action_type нет observable fields — нужна confirmation продавца.",
            )

        evidence: list[str] = []
        missing_fields: list[str] = []
        field_results: dict[str, str] = {}
        target_changed = False
        target_unchanged = False
        any_comparable = False
        actual_values: dict[str, Any] = {}
        use_equal_target = (
            (spec.expected_change or "").upper() == "EQUAL" and bool(expected_values)
        )

        for f in fields:
            a = cur.get(f)
            b = base.get(f)
            if use_equal_target and f in expected_values:
                expected = expected_values[f]
                if a is None:
                    missing_fields.append(f)
                    field_results[f] = "UNAVAILABLE"
                    continue
                any_comparable = True
                actual_values[f] = a
                matched = _field_matches_expected(a, expected)
                if matched:
                    target_changed = True
                    field_results[f] = "APPLIED"
                    evidence.append(f"{f}_matches_expected")
                elif b is not None and a == b:
                    target_unchanged = True
                    field_results[f] = "NOT_APPLIED"
                    evidence.append(f"{f}_unchanged")
                else:
                    # changed, but not to exact expected → still APPLIED + actual_value
                    target_changed = True
                    field_results[f] = "APPLIED"
                    evidence.append(f"{f}_changed_actual_value")
                continue
            ch = _field_changed(b, a, spec.expected_change)
            if ch is None:
                missing_fields.append(f)
                field_results[f] = "UNAVAILABLE"
                continue
            any_comparable = True
            actual_values[f] = a
            if ch:
                target_changed = True
                field_results[f] = "APPLIED"
                evidence.append(f"{f}_changed")
            else:
                target_unchanged = True
                field_results[f] = "NOT_APPLIED"
                evidence.append(f"{f}_unchanged")
            if f == "characteristics_hash":
                evidence.append(f"characteristics_{characteristics_delta(b, a).lower()}")

        # detect unrelated changes
        other: list[str] = []
        watch = {
            "price", "main_photo_hash", "photo_set_hash", "title_hash",
            "description_hash", "characteristics_hash", "stock", "ad_spend", "cost",
        }
        for f in watch:
            if f in fields:
                continue
            ch = _field_changed(base.get(f), cur.get(f), "DIFFERENT")
            if ch is True:
                other.append(f)

        if not any_comparable:
            return VerificationResult(
                status=VerificationStatus.INCONCLUSIVE,
                source=source,
                evidence=evidence,
                other_changes=other,
                needs_seller_confirmation=True,
                message=(
                    "Поля для проверки недоступны "
                    f"({', '.join(missing_fields) or '—'}). Нужно подтверждение продавца."
                ),
                details={"missing_fields": missing_fields, "field_results": field_results},
            )

        rec_at = recommendation_at
        if rec_at is None:
            rec_at = action.accepted_at or action.created_at or action.executed_at
        change_ts = cur.timestamp or cur.source_timestamp

        details = {
            "missing_fields": missing_fields,
            "field_results": field_results,
            "actual_values": actual_values,
        }

        # Partial: some applied, some not
        if target_changed and target_unchanged:
            causal = CausalAttribution.ELIGIBLE
            if action.action_group_id or (action.metadata or {}).get("multiple_interventions"):
                causal = CausalAttribution.MIXED_INTERVENTION
            return VerificationResult(
                status=VerificationStatus.APPLIED_PARTIAL,
                source=source,
                evidence=evidence,
                target_applied=None,
                other_changes=other,
                causal_attribution=causal,
                needs_seller_confirmation=False,
                message=(
                    "Частичное применение: часть целевых полей изменилась, часть — нет. "
                    "Это не SUCCESS и не FAILED."
                ),
                details=details,
            )

        if target_changed:
            causal = CausalAttribution.ELIGIBLE
            if rec_at is not None and change_ts is not None and float(change_ts) < float(rec_at):
                causal = CausalAttribution.UNKNOWN
                evidence.append("change_timestamp_before_recommendation")
            if action.action_group_id or (action.metadata or {}).get("multiple_interventions"):
                causal = CausalAttribution.MIXED_INTERVENTION
                evidence.append("multiple_interventions")
            msg = "Целевое изменение подтверждено по snapshot."
            if other:
                msg += f" Также изменились другие поля: {', '.join(other)}."
            if any(e.endswith("_changed_actual_value") for e in evidence):
                msg += f" Фактические значения: {actual_values}."
            return VerificationResult(
                status=VerificationStatus.APPLIED,
                source=source,
                evidence=evidence,
                target_applied=True,
                other_changes=other,
                causal_attribution=causal,
                needs_seller_confirmation=False,
                message=msg,
                details=details,
            )

        # target unchanged
        msg = "Целевое поле не изменилось — рекомендация пока не считается выполненной."
        if other:
            msg += (
                f" При этом обнаружены другие изменения ({', '.join(other)}) — "
                "это отдельное вмешательство, не доказательство выполнения рекомендации."
            )
        return VerificationResult(
            status=VerificationStatus.NOT_APPLIED,
            source=source,
            evidence=evidence,
            target_applied=False,
            other_changes=other,
            causal_attribution=CausalAttribution.NOT_EVALUABLE,
            needs_seller_confirmation=False,
            message=msg,
            details=details,
        )

    def verify(
        self,
        action: SellerAction,
        *,
        api_snapshot: SnapshotView | dict[str, Any] | None = None,
        parser_snapshot: SnapshotView | dict[str, Any] | None = None,
        baseline: SnapshotView | dict[str, Any] | None = None,
        seller_intent: SellerConfirmIntent | str | None = None,
        recommendation_at: float | None = None,
        allow_seller_prompt: bool = True,
    ) -> VerificationResult:
        """
        Order: API -> parser -> seller confirmation.
        Elapsed check_after alone never yields APPLIED.
        """
        spec = action.verification_spec or VerificationSpec.for_action_type(action.action_type)
        methods = [m.upper() for m in (spec.method or [])]
        base = baseline
        if base is None and (action.metadata or {}).get("baseline_fields"):
            base = SnapshotView.from_mapping((action.metadata or {}).get("baseline_fields"))

        if base is None:
            if seller_intent:
                return self.apply_seller_confirmation(action, seller_intent)
            return VerificationResult(
                status=VerificationStatus.INCONCLUSIVE,
                source="unavailable",
                needs_seller_confirmation=allow_seller_prompt,
                message="Нет baseline snapshot — автоматически проверить применение нельзя.",
            )

        inconclusive_like = (
            VerificationStatus.NOT_VERIFIABLE,
            VerificationStatus.INCONCLUSIVE,
        )

        if "API" in methods and api_snapshot is not None:
            res = self.compare_snapshots(
                action, base, api_snapshot, source="api", recommendation_at=recommendation_at,
            )
            if res.status not in inconclusive_like:
                return res

        if "PARSER" in methods and parser_snapshot is not None:
            res = self.compare_snapshots(
                action, base, parser_snapshot, source="parser", recommendation_at=recommendation_at,
            )
            if res.status not in inconclusive_like:
                return res

        if seller_intent:
            return self.apply_seller_confirmation(action, seller_intent)

        if "SELLER_CONFIRMATION" in methods and allow_seller_prompt:
            return VerificationResult(
                status=VerificationStatus.INCONCLUSIVE,
                source="unavailable",
                needs_seller_confirmation=True,
                message=self.format_seller_prompt(action),
            )

        return VerificationResult(
            status=VerificationStatus.UNKNOWN,
            source="unavailable",
            needs_seller_confirmation=False,
            message="Доказательств изменения нет.",
        )

    def resolve_conflict(
        self,
        action: SellerAction,
        objective: VerificationResult,
    ) -> VerificationResult | None:
        seller_yes = (
            action.verification_status is VerificationStatus.SELLER_CONFIRMED
            or (
                action.verification_source == "seller_confirmation"
                and "seller_said_yes" in (action.verification_evidence or [])
            )
        )
        if not seller_yes:
            return None
        if objective.status is not VerificationStatus.NOT_APPLIED:
            return None
        return VerificationResult(
            status=VerificationStatus.NEEDS_REVIEW,
            source=objective.source or "api",
            evidence=list(objective.evidence) + ["seller_confirmation_conflict"],
            target_applied=None,
            other_changes=list(objective.other_changes),
            causal_attribution=CausalAttribution.UNKNOWN,
            needs_seller_confirmation=False,
            message=(
                "⚠️ Есть расхождение\n"
                "Вы подтвердили изменение, но по последнему доступному snapshot "
                "целевое поле пока совпадает с исходным.\n"
                "Статус: NEEDS_REVIEW."
            ),
            details={
                "conflict": True,
                "seller_status": action.verification_status.value,
            },
        )

    def apply_seller_confirmation(
        self,
        action: SellerAction,
        intent: SellerConfirmIntent | str,
    ) -> VerificationResult:
        if not isinstance(intent, SellerConfirmIntent):
            intent = SellerConfirmIntent(str(intent))
        if intent is SellerConfirmIntent.YES_APPLIED:
            return VerificationResult(
                status=VerificationStatus.SELLER_CONFIRMED,
                source="seller_confirmation",
                evidence=["seller_said_yes"],
                target_applied=True,
                causal_attribution=CausalAttribution.ELIGIBLE,
                needs_seller_confirmation=False,
                message="Продавец подтвердил применение. Это ещё не SUCCESS — нужен outcome по метрикам.",
            )
        if intent is SellerConfirmIntent.NO_NOT_APPLIED:
            return VerificationResult(
                status=VerificationStatus.NOT_APPLIED,
                source="seller_confirmation",
                evidence=["seller_said_no"],
                target_applied=False,
                causal_attribution=CausalAttribution.NOT_EVALUABLE,
                message="Продавец указал, что действие не выполнено. Не считаем рекомендацию FAILED.",
            )
        if intent is SellerConfirmIntent.NOT_YET:
            return VerificationResult(
                status=VerificationStatus.PENDING,
                source="seller_confirmation",
                evidence=["seller_said_not_yet"],
                target_applied=False,
                causal_attribution=CausalAttribution.NOT_EVALUABLE,
                message="Действие отложено. Проверка повторится позже.",
            )
        return VerificationResult(
            status=VerificationStatus.UNKNOWN,
            source="unavailable",
            evidence=["seller_asked_check_again"],
            needs_seller_confirmation=False,
            message="Повторная автоматическая проверка запрошена.",
            details={"retry": True},
        )

    def format_auto_applied(self, action: SellerAction, result: VerificationResult) -> str:
        label = action.recommendation or action.action_type.value
        if action.outcome_after:
            outcome_hint = (
                f"\nТеперь оставляем действие в наблюдении "
                f"до {self._time.format_seller(action.outcome_after)} "
                f"и посмотрим на CTR, CVR и заказы."
            )
        else:
            outcome_hint = "\nТеперь наблюдаем эффект. APPLIED ≠ SUCCESS."
        if result.status is VerificationStatus.APPLIED_PARTIAL:
            fr = (result.details or {}).get("field_results") or {}
            return (
                f"✅ Частичное подтверждение\n\n"
                f"Часть полей изменилась: {fr}.\n"
                f"Действие: {label[:200]} (арт. {action.article}).\n"
                f"Это не SUCCESS и не FAILED."
                f"{outcome_hint}"
            )
        return (
            f"✅ Изменение подтверждено\n\n"
            f"ARGUS подтвердил изменение автоматически через {result.source}.\n"
            f"Действие: {label[:200]} (арт. {action.article}).\n"
            f"Это подтверждение факта изменения, не успеха рекомендации."
            f"{outcome_hint}"
        )

    def format_seller_prompt(self, action: SellerAction) -> str:
        label = {
            ActionType.CHANGE_MAIN_PHOTO: "заменить главное фото",
            ActionType.CHANGE_PRICE: "изменить цену",
            ActionType.CHANGE_TITLE: "изменить название",
            ActionType.CHANGE_DESCRIPTION: "изменить описание",
        }.get(action.action_type, action.recommendation or action.action_type.value)
        return (
            "⏱ Проверка действия\n\n"
            f"Ранее мы рекомендовали: {label} (арт. {action.article}).\n"
            "Я не могу автоматически подтвердить изменение через подключённые источники.\n"
            "Ты действительно сделал это?"
        )

    def format_check_due_api_ready(self, action: SellerAction) -> str:
        label = action.recommendation or action.action_type.value
        return (
            "⏱ Проверка действия\n\n"
            f"Подошло время проверить: {label[:200]} (арт. {action.article}).\n"
            "Я могу проверить результат автоматически через подключённый API."
        )

    def format_result_message(self, action: SellerAction, result: VerificationResult) -> str:
        if result.status is VerificationStatus.NEEDS_REVIEW:
            return result.message
        if result.status is VerificationStatus.APPLIED_PARTIAL:
            return self.format_auto_applied(action, result)
        if result.needs_seller_confirmation and result.status in (
            VerificationStatus.NOT_VERIFIABLE,
            VerificationStatus.INCONCLUSIVE,
            VerificationStatus.UNKNOWN,
        ):
            return result.message or self.format_seller_prompt(action)
        if result.status is VerificationStatus.APPLIED:
            return self.format_auto_applied(action, result)
        if result.status is VerificationStatus.SELLER_CONFIRMED:
            return (
                f"✅ Принял подтверждение продавца.\n{result.message}\n"
                "SELLER_CONFIRMED ≠ SUCCESS — нужны метрики для outcome."
            )
        if result.status is VerificationStatus.NOT_APPLIED:
            return f"🔔 Проверка рекомендации\n{result.message}"
        if result.status is VerificationStatus.PENDING:
            return result.message
        if result.status is VerificationStatus.INCONCLUSIVE:
            return result.message or "Проверка INCONCLUSIVE — данных недостаточно."
        return result.message or "Статус проверки неизвестен."
