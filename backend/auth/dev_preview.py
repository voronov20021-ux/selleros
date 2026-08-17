"""Local-only Mini App DEV preview identity.

Not a product module. Never a real Telegram user and never real WB keys.
POST /api/auth/dev is fail-closed: flag + loopback request, else 404/403.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from fastapi import Request

DEV_PREVIEW_SELLER_ID = "dev-preview"
DEV_PREVIEW_DISPLAY_NAME = "Local Preview"
DEV_PREVIEW_USERNAME = "local-preview"

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})
_PRODUCTION_ENVS = frozenset({"production", "prod"})


def _hostname(value: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    if raw.startswith("["):
        end = raw.find("]")
        return raw[: end + 1] if end >= 0 else raw
    return raw.split(":", 1)[0]


def _is_local_host(host: str) -> bool:
    return _hostname(host) in _LOCAL_HOSTS


def is_loopback_request(request: Request) -> bool:
    """True only when Host (and Origin, if present) are loopback.

    Client IP must also be loopback. Starlette TestClient reports
    ``testclient``; that is accepted only when Host is already local
    (a remote client cannot present that ASGI client host).
    """
    host = request.headers.get("host") or ""
    if not _is_local_host(host):
        return False

    origin = (request.headers.get("origin") or "").strip()
    if origin:
        parsed = urlparse(origin)
        if not _is_local_host(parsed.hostname or ""):
            return False

    client = (request.client.host if request.client else "") or ""
    if _is_local_host(client):
        return True
    if client in {"testclient", "testserver"}:
        return True
    return False


def miniapp_dev_auth_allowed(request: Request) -> tuple[bool, str]:
    """Return (ok, reason). Fail closed. Never honor missing initData."""
    from backend import config

    if not getattr(config, "MINIAPP_DEV_AUTH", False):
        return False, "disabled"

    app_env = (getattr(config, "APP_ENV", "") or "").strip().lower()
    if app_env in _PRODUCTION_ENVS:
        return False, "production"

    if not is_loopback_request(request):
        return False, "not_local"

    return True, "ok"
