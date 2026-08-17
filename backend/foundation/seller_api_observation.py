"""
seller_api_observation.py — production Seller API observation for Action loop.

Hierarchy of evidence (this module is the API tier only):
  SELLER API → (caller falls back to) PARSER/CACHE → SELLER CONFIRMATION → UNKNOWN

Does NOT:
  - invent metrics
  - call BrowserFetcher
  - diagnose SUCCESS/FAILED
  - mutate seller card

Returns facts + per-field provenance only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Mapping

from backend.foundation.action_verification import hash_value, normalize_card_fields
from backend.foundation.time_service import TimeService, get_time_service

log = logging.getLogger("selleros.foundation.seller_api_observation")


class FieldProvenance(str, Enum):
    KNOWN = "KNOWN"
    MISSING = "MISSING"          # field requested, API answered empty
    UNAVAILABLE = "UNAVAILABLE"  # API down / no credentials / endpoint not ready
    NOT_INCLUDED = "NOT_INCLUDED"  # not requested this turn


# Optional live adapter: (seller_id, product_id, fields) → raw field map or None
SellerApiAdapter = Callable[
    [str, str, list[str]],
    Awaitable[dict[str, Any] | None],
]
# Credential resolver: seller_id → api_key | None
CredentialResolver = Callable[[str], Awaitable[str | None] | str | None]


@dataclass
class FieldObservation:
    name: str
    value: Any = None
    provenance: FieldProvenance = FieldProvenance.UNAVAILABLE
    source_timestamp: float | None = None
    stale: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "provenance": self.provenance.value,
            "source_timestamp": self.source_timestamp,
            "stale": self.stale,
        }


@dataclass
class SellerAPIObservationResult:
    """Structured API observation — facts only, no business diagnosis."""

    seller_id: str
    product_id: str
    available: bool
    fields: dict[str, FieldObservation] = field(default_factory=dict)
    snapshot_source: str = "WB_SELLER_API"
    timestamp: float | None = None
    quality: str = "unknown"
    api_connected: bool = False
    error: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def known_values(self) -> dict[str, Any]:
        return {
            k: fo.value
            for k, fo in self.fields.items()
            if fo.provenance is FieldProvenance.KNOWN and fo.value is not None
        }

    def provenance_map(self) -> dict[str, str]:
        return {k: fo.provenance.value for k, fo in self.fields.items()}

    def to_dict(self) -> dict[str, Any]:
        return {
            "seller_id": self.seller_id,
            "product_id": self.product_id,
            "available": self.available,
            "fields": {k: v.to_dict() for k, v in self.fields.items()},
            "snapshot_source": self.snapshot_source,
            "timestamp": self.timestamp,
            "quality": self.quality,
            "api_connected": self.api_connected,
            "error": self.error,
            "details": dict(self.details),
            "known_values": self.known_values(),
            "provenance": self.provenance_map(),
        }

    def as_flat_fields(self) -> dict[str, Any]:
        """Flatten for ActionVerificationEngine compare (KNOWN only)."""
        out = self.known_values()
        if self.timestamp is not None:
            out["timestamp"] = self.timestamp
        out["source"] = "api"
        out["quality"] = self.quality
        out["snapshot_source"] = self.snapshot_source
        return out


def _normalize_requested(fields: list[str] | None) -> list[str]:
    if not fields:
        return [
            "price", "main_photo_hash", "main_photo_url", "title", "title_hash",
            "description", "description_hash", "stock", "orders", "clicks",
            "impressions", "ctr", "cvr", "revenue", "cost", "profit",
        ]
    return [str(f).strip() for f in fields if str(f).strip()]


def _enrich_hashes(raw: dict[str, Any]) -> dict[str, Any]:
    """Derive hash fields from raw text/urls without inventing values."""
    out = dict(raw)
    if "main_photo_hash" not in out and out.get("main_photo_url"):
        out["main_photo_hash"] = hash_value(out["main_photo_url"])
    if "main_photo_hash" not in out and out.get("main_photo"):
        out["main_photo_hash"] = hash_value(out["main_photo"])
    if "title_hash" not in out and out.get("title"):
        out["title_hash"] = hash_value(out["title"])
    if "description_hash" not in out and out.get("description"):
        out["description_hash"] = hash_value(out["description"])
    return out


class SellerAPIObservationProvider:
    """
    Production-ready Seller API observation abstraction.

    Real WB content/analytics endpoints are pluggable via ``api_adapter``.
    Without adapter + credentials → honest UNAVAILABLE (never invent).
    """

    def __init__(
        self,
        *,
        api_adapter: SellerApiAdapter | None = None,
        credential_resolver: CredentialResolver | None = None,
        time_service: TimeService | None = None,
        max_age_sec: float = 86400.0,
        global_api_key: str | None = None,
    ) -> None:
        self._adapter = api_adapter
        self._creds = credential_resolver
        self._time = time_service or get_time_service()
        self._max_age = float(max_age_sec)
        self._global_key = (global_api_key or "").strip() or None

    async def _resolve_key(self, seller_id: str) -> str | None:
        if self._creds is not None:
            try:
                key = self._creds(seller_id)
                if hasattr(key, "__await__"):
                    key = await key  # type: ignore[misc]
                if key:
                    return str(key).strip() or None
            except Exception as exc:
                log.debug("credential resolve failed: %s", exc)
        return self._global_key

    async def observe_product(
        self,
        seller_id: str,
        product_id: str,
        fields: list[str] | None = None,
    ) -> SellerAPIObservationResult:
        sid = str(seller_id)
        pid = str(product_id)
        requested = _normalize_requested(fields)
        now = self._time.timestamp()

        key = await self._resolve_key(sid)
        if not key and self._adapter is None:
            # No credentials and no injected adapter → cannot observe via API
            return SellerAPIObservationResult(
                seller_id=sid,
                product_id=pid,
                available=False,
                fields={
                    f: FieldObservation(name=f, provenance=FieldProvenance.UNAVAILABLE)
                    for f in requested
                },
                timestamp=now,
                quality="unavailable",
                api_connected=False,
                error="no_api_credentials",
            )

        raw: dict[str, Any] | None = None
        error: str | None = None
        if self._adapter is not None:
            try:
                raw = await self._adapter(sid, pid, requested)
            except Exception as exc:
                log.info("Seller API adapter error seller=%s product=%s: %s", sid, pid, type(exc).__name__)
                error = f"adapter_error:{type(exc).__name__}"
                raw = None
        else:
            # Credentials exist but production adapter not wired yet — honest UNAVAILABLE
            error = "seller_api_endpoint_not_implemented"
            raw = None

        if raw is None:
            return SellerAPIObservationResult(
                seller_id=sid,
                product_id=pid,
                available=False,
                fields={
                    f: FieldObservation(name=f, provenance=FieldProvenance.UNAVAILABLE)
                    for f in requested
                },
                timestamp=now,
                quality="unavailable",
                api_connected=bool(key),
                error=error or "api_unavailable",
                details={"has_credentials": bool(key)},
            )

        raw = _enrich_hashes(dict(raw))
        src_ts = raw.get("timestamp") or raw.get("source_timestamp") or now
        try:
            src_ts_f = float(src_ts)
        except (TypeError, ValueError):
            src_ts_f = now
        stale = (now - src_ts_f) > self._max_age if src_ts_f else False

        field_map: dict[str, FieldObservation] = {}
        known_count = 0
        for f in requested:
            if f not in raw or raw.get(f) is None:
                # distinguish missing vs not in payload
                if f in raw and raw.get(f) is None:
                    prov = FieldProvenance.MISSING
                else:
                    prov = FieldProvenance.MISSING
                field_map[f] = FieldObservation(
                    name=f, value=None, provenance=prov, source_timestamp=src_ts_f, stale=stale,
                )
            else:
                known_count += 1
                field_map[f] = FieldObservation(
                    name=f,
                    value=raw[f],
                    provenance=FieldProvenance.KNOWN,
                    source_timestamp=src_ts_f,
                    stale=stale,
                )

        quality = "high" if known_count == len(requested) else ("medium" if known_count else "low")
        if stale:
            quality = "stale"
        return SellerAPIObservationResult(
            seller_id=sid,
            product_id=pid,
            available=known_count > 0,
            fields=field_map,
            snapshot_source=str(raw.get("snapshot_source") or "WB_SELLER_API"),
            timestamp=src_ts_f,
            quality=quality,
            api_connected=True,
            error=None,
            details={"stale": stale, "known_count": known_count, "requested": list(requested)},
        )

    async def observe_from_mapping(
        self,
        seller_id: str,
        product_id: str,
        data: Mapping[str, Any],
        fields: list[str] | None = None,
        *,
        source_timestamp: float | None = None,
    ) -> SellerAPIObservationResult:
        """Test/helper: treat a dict as API payload (still provenance-aware)."""

        async def _adapter(_s: str, _p: str, _f: list[str]) -> dict[str, Any]:
            payload = dict(data)
            if source_timestamp is not None:
                payload.setdefault("timestamp", source_timestamp)
            return payload

        prev = self._adapter
        self._adapter = _adapter
        try:
            return await self.observe_product(seller_id, product_id, fields)
        finally:
            self._adapter = prev


def seller_data_to_api_fields(seller_data) -> dict[str, Any]:
    """Map SellerData (source=api fields only preferred) → observation dict. No invention."""
    if seller_data is None:
        return {}
    out: dict[str, Any] = {}
    for name in (
        "price", "ctr", "cvr", "orders", "sales", "impressions", "views",
        "ad_spend", "cost", "returns",
    ):
        val = getattr(seller_data, name, None)
        if val is None:
            continue
        src = getattr(seller_data, f"{name}_source", None)
        # Prefer API-sourced; allow explicit user only when tagged — caller decides.
        out[name] = val
        if src:
            out[f"{name}_source"] = src
    return out


def product_to_api_like_fields(product) -> dict[str, Any]:
    """Normalize a product-like object into observable fields (for injected adapters)."""
    return normalize_card_fields(product)
