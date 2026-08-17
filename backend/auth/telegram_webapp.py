"""Official Telegram Web Apps initData validation (HMAC-SHA256)."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Mapping, Optional
from urllib.parse import parse_qsl


@dataclass(frozen=True)
class TelegramWebAppUser:
    """Identity extracted from validated Telegram WebApp initData."""

    telegram_user_id: str
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    language_code: Optional[str] = None
    auth_date: int = 0
    raw_user: Optional[dict[str, Any]] = None

    @property
    def seller_id(self) -> str:
        """Seller identity for MVP = telegram_user_id string."""
        return self.telegram_user_id

    @property
    def display_name(self) -> str:
        parts = [p for p in (self.first_name, self.last_name) if p]
        if parts:
            return " ".join(parts)
        if self.username:
            return f"@{self.username}"
        return f"seller_{self.telegram_user_id}"


class TelegramAuthError(ValueError):
    """Raised when initData is missing, forged, or expired."""

    def __init__(self, message: str, *, code: str = "invalid"):
        super().__init__(message)
        self.code = code


def _parse_init_data(init_data: str) -> dict[str, str]:
    if not init_data or not str(init_data).strip():
        raise TelegramAuthError("initData is required", code="missing")
    pairs = parse_qsl(str(init_data).strip(), keep_blank_values=True)
    if not pairs:
        raise TelegramAuthError("initData is empty or malformed", code="malformed")
    return {k: v for k, v in pairs}


def build_data_check_string(fields: Mapping[str, str]) -> str:
    """All fields except hash, sorted by key, key=value joined with \\n."""
    items = [(k, v) for k, v in fields.items() if k != "hash"]
    items.sort(key=lambda kv: kv[0])
    return "\n".join(f"{k}={v}" for k, v in items)


def compute_webapp_hash(bot_token: str, data_check_string: str) -> str:
    """
    secret_key = HMAC_SHA256(key=\"WebAppData\", msg=bot_token)
    computed_hash = HMAC_SHA256(key=secret_key, msg=data_check_string).hex()
    """
    secret_key = hmac.new(
        key=b"WebAppData",
        msg=bot_token.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    return hmac.new(
        key=secret_key,
        msg=data_check_string.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()


def validate_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_seconds: int = 86400,
    now: Optional[float] = None,
) -> TelegramWebAppUser:
    """
    Validate Telegram Mini App initData with the official algorithm.

    Never trust client-supplied user_id without a valid hash.
    """
    if not bot_token:
        raise TelegramAuthError("bot token is not configured", code="config")

    fields = _parse_init_data(init_data)
    received_hash = fields.get("hash")
    if not received_hash:
        raise TelegramAuthError("hash is missing", code="missing_hash")

    data_check_string = build_data_check_string(fields)
    expected = compute_webapp_hash(bot_token, data_check_string)
    if not hmac.compare_digest(expected, received_hash):
        raise TelegramAuthError("initData hash mismatch", code="bad_hash")

    auth_date_raw = fields.get("auth_date")
    if not auth_date_raw:
        raise TelegramAuthError("auth_date is missing", code="missing_auth_date")
    try:
        auth_date = int(auth_date_raw)
    except ValueError as exc:
        raise TelegramAuthError("auth_date is invalid", code="bad_auth_date") from exc

    ts = time.time() if now is None else float(now)
    if max_age_seconds > 0 and (ts - auth_date) > max_age_seconds:
        raise TelegramAuthError("initData auth_date expired", code="expired")
    if auth_date > ts + 60:
        # clock skew guard — reject far-future dates
        raise TelegramAuthError("initData auth_date is in the future", code="bad_auth_date")

    user_raw = fields.get("user")
    if not user_raw:
        raise TelegramAuthError("user field is missing", code="missing_user")
    try:
        user_obj = json.loads(user_raw)
    except json.JSONDecodeError as exc:
        raise TelegramAuthError("user field is not valid JSON", code="bad_user") from exc
    if not isinstance(user_obj, dict):
        raise TelegramAuthError("user field must be an object", code="bad_user")

    uid = user_obj.get("id")
    if uid is None:
        raise TelegramAuthError("user.id is missing", code="missing_user_id")
    telegram_user_id = str(uid)

    return TelegramWebAppUser(
        telegram_user_id=telegram_user_id,
        username=(str(user_obj["username"]) if user_obj.get("username") else None),
        first_name=(str(user_obj["first_name"]) if user_obj.get("first_name") else None),
        last_name=(str(user_obj["last_name"]) if user_obj.get("last_name") else None),
        language_code=(
            str(user_obj["language_code"]) if user_obj.get("language_code") else None
        ),
        auth_date=auth_date,
        raw_user=user_obj,
    )


def mint_init_data(
    bot_token: str,
    *,
    user_id: int,
    username: str = "testuser",
    first_name: str = "Test",
    last_name: str = "Seller",
    auth_date: Optional[int] = None,
    extra: Optional[Mapping[str, str]] = None,
) -> str:
    """
    Build a signed initData string for tests only.
    Uses the same HMAC algorithm as Telegram.
    """
    from urllib.parse import quote

    user = {
        "id": int(user_id),
        "first_name": first_name,
        "last_name": last_name,
        "username": username,
        "language_code": "ru",
    }
    fields: dict[str, str] = {
        "auth_date": str(int(time.time()) if auth_date is None else auth_date),
        "query_id": "AAEtest",
        "user": json.dumps(user, separators=(",", ":"), ensure_ascii=False),
    }
    if extra:
        fields.update({k: str(v) for k, v in extra.items() if k != "hash"})

    data_check_string = build_data_check_string(fields)
    fields["hash"] = compute_webapp_hash(bot_token, data_check_string)

    # encode like Telegram (query-string); user JSON must stay as-is after parse_qsl
    parts = []
    for key in sorted(fields.keys()):
        parts.append(f"{quote(key, safe='')}={quote(fields[key], safe='')}")
    return "&".join(parts)
