"""
action_observation.py — fresh observable snapshot for Action verification.

Priority: API → existing parser/cache/metric snapshot → unavailable.
Does NOT import or call BrowserFetcher. Uses ProductService.get_product_snapshot
(SFP) when provided, or MemoryStore metric snapshots / injected callables.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from backend.foundation.action_verification import SnapshotView, normalize_card_fields
from backend.foundation.time_service import TimeService, get_time_service

log = logging.getLogger("selleros.foundation.action_observation")

ApiFetcher = Callable[[int, int], Awaitable[dict[str, Any] | None]]
# (seller_id, article) → fields dict or None


@dataclass
class ObservationResult:
    """One observation attempt for verification (not outcome)."""

    available: bool
    verification_source: str  # api | parser | unavailable
    snapshot_source: str | None = None  # WB_SELLER_API | PUBLIC_CACHE | METRIC_SNAPSHOT | ...
    fields: dict[str, Any] = field(default_factory=dict)
    snapshot_id: int | None = None
    timestamp: float | None = None
    quality: str = "unknown"
    browser_called: bool = False
    cache_status: str | None = None
    error: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_snapshot_view(self) -> SnapshotView | None:
        if not self.available or not self.fields:
            return None
        payload = dict(self.fields)
        if self.timestamp is not None:
            payload.setdefault("timestamp", self.timestamp)
        if self.snapshot_source:
            payload.setdefault("source", self.snapshot_source.lower())
        payload.setdefault("quality", self.quality)
        return SnapshotView.from_mapping(payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "verification_source": self.verification_source,
            "snapshot_source": self.snapshot_source,
            "fields": dict(self.fields),
            "snapshot_id": self.snapshot_id,
            "timestamp": self.timestamp,
            "quality": self.quality,
            "browser_called": self.browser_called,
            "cache_status": self.cache_status,
            "error": self.error,
            "details": dict(self.details),
        }


def _metric_snap_to_fields(snap: Any) -> dict[str, Any]:
    """ProductMetricSnapshot → observable + metric fields (no invention)."""
    out: dict[str, Any] = {}
    if snap is None:
        return out
    if isinstance(snap, dict):
        data = snap
    else:
        data = {
            k: getattr(snap, k, None)
            for k in (
                "id", "price", "rating", "feedbacks", "stock", "ctr", "cvr",
                "orders", "sales", "revenue", "ad_spend", "cost", "profit",
                "margin", "captured_at", "source", "provenance",
            )
        }
    for k, v in data.items():
        if k in ("id", "captured_at", "source", "provenance"):
            continue
        if v is not None:
            out[k] = v
    if data.get("captured_at") is not None:
        out["timestamp"] = float(data["captured_at"])
    if data.get("source"):
        out["source"] = data["source"]
    return out


class ActionObservationProvider:
    """
    Deterministic observation layer for ActionVerificationScheduler.

    Never starts BrowserFetcher itself. Optional product_service may call
    get_product_snapshot (cache-first SFP); browser_called is read from
    last_fetch_decision when present.
    """

    def __init__(
        self,
        *,
        memory_store=None,
        product_service=None,
        api_fetcher: ApiFetcher | None = None,
        seller_api_provider=None,
        time_service: TimeService | None = None,
        allow_product_fetch: bool = True,
        observe_fields: list[str] | None = None,
    ) -> None:
        self._store = memory_store
        self._products = product_service
        self._api = api_fetcher
        self._seller_api = seller_api_provider
        self._time = time_service or get_time_service()
        self._allow_product_fetch = allow_product_fetch
        self._observe_fields = observe_fields

    async def observe(
        self,
        seller_id: int,
        article: int,
        *,
        marketplace: str = "wildberries",
        session_product=None,
        fields: list[str] | None = None,
    ) -> ObservationResult:
        article = int(article)
        seller_id = int(seller_id)
        req_fields = fields or self._observe_fields

        # 1a) Production SellerAPIObservationProvider
        if self._seller_api is not None:
            try:
                api_res = await self._seller_api.observe_product(
                    str(seller_id), str(article), req_fields,
                )
                if api_res.available:
                    flat = api_res.as_flat_fields()
                    return ObservationResult(
                        available=True,
                        verification_source="api",
                        snapshot_source=api_res.snapshot_source,
                        fields=flat,
                        timestamp=api_res.timestamp or self._time.timestamp(),
                        quality=api_res.quality,
                        browser_called=False,
                        cache_status="API",
                        details={
                            "provenance": api_res.provenance_map(),
                            "api_connected": api_res.api_connected,
                            "stale": bool((api_res.details or {}).get("stale")),
                        },
                    )
                # API connected but no known fields → try parser fallback below
                if api_res.error and not api_res.api_connected:
                    pass
            except Exception as exc:
                log.debug("seller_api_provider observe skip: %s", exc)

        # 1b) Legacy injected api_fetcher
        if self._api is not None:
            try:
                raw = await self._api(seller_id, article)
            except Exception as exc:
                log.debug("API observation failed article=%s: %s", article, exc)
                raw = None
            if raw:
                fields_out = dict(raw)
                if "main_photo_hash" not in fields_out and session_product is not None:
                    fields_out.update(
                        {k: v for k, v in normalize_card_fields(session_product).items() if k not in fields_out}
                    )
                return ObservationResult(
                    available=True,
                    verification_source="api",
                    snapshot_source=str(fields_out.pop("snapshot_source", None) or "WB_SELLER_API"),
                    fields=fields_out,
                    timestamp=float(fields_out.get("timestamp") or self._time.timestamp()),
                    quality=str(fields_out.get("quality") or "high"),
                    browser_called=False,
                    cache_status="API",
                )

        # 2) Existing parser path via ProductService snapshot (SFP / cache-first)
        if self._allow_product_fetch and self._products is not None:
            try:
                product = None
                if hasattr(self._products, "get_product_snapshot"):
                    product = await self._products.get_product_snapshot(
                        marketplace,
                        article,
                        session_product=session_product,
                        force_refresh=False,
                    )
                elif hasattr(self._products, "get_product"):
                    # Prefer not to use raw get_product — may hit Browser.
                    # Only if public cache path exposed separately.
                    product = None
                decision = getattr(self._products, "last_fetch_decision", None) or {}
                browser_called = bool(decision.get("browser_allowed")) and not decision.get("reused")
                # If reused from cache, browser was not called
                if decision.get("cache_status") in ("HIT", "SESSION") or decision.get("reused"):
                    browser_called = False
                if product is not None:
                    fields = normalize_card_fields(product)
                    fields["timestamp"] = self._time.timestamp()
                    fields["source"] = "parser"
                    return ObservationResult(
                        available=True,
                        verification_source="parser",
                        snapshot_source=(
                            "PUBLIC_CACHE"
                            if (decision.get("cache_status") in ("HIT", "SESSION") or decision.get("reused"))
                            else "PRODUCT_SERVICE"
                        ),
                        fields=fields,
                        timestamp=fields["timestamp"],
                        quality="medium",
                        browser_called=browser_called,
                        cache_status=str(decision.get("cache_status") or "UNKNOWN"),
                        details={"fetch_decision": dict(decision)},
                    )
            except Exception as exc:
                log.debug("parser observation skip article=%s: %s", article, exc)

        # 3) Latest metric / analysis snapshot from MemoryStore (no Browser)
        if self._store is not None and hasattr(self._store, "list_metric_snapshots"):
            try:
                snaps = await self._store.list_metric_snapshots(
                    seller_id, article, marketplace=marketplace, limit=5,
                )
                if snaps:
                    snap = snaps[-1]  # newest (store returns ASC)
                    fields = _metric_snap_to_fields(snap)
                    if fields:
                        sid = getattr(snap, "id", None) if not isinstance(snap, dict) else snap.get("id")
                        return ObservationResult(
                            available=True,
                            verification_source="parser",
                            snapshot_source="METRIC_SNAPSHOT",
                            fields=fields,
                            snapshot_id=int(sid) if sid is not None else None,
                            timestamp=float(fields.get("timestamp") or self._time.timestamp()),
                            quality="medium",
                            browser_called=False,
                            cache_status="MEMORY",
                        )
            except Exception as exc:
                log.debug("metric snapshot observation skip: %s", exc)

        # 4) Session product only (already in memory — no fetch)
        if session_product is not None:
            fields = normalize_card_fields(session_product)
            if fields:
                return ObservationResult(
                    available=True,
                    verification_source="parser",
                    snapshot_source="SESSION_PRODUCT",
                    fields=fields,
                    timestamp=self._time.timestamp(),
                    quality="low",
                    browser_called=False,
                    cache_status="SESSION",
                )

        return ObservationResult(
            available=False,
            verification_source="unavailable",
            snapshot_source=None,
            browser_called=False,
            error="no_api_no_parser_no_snapshot",
        )
