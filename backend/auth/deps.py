"""FastAPI dependencies for Mini App session auth."""

from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException, Request

from backend.auth.session import SellerSession, SessionStore


def get_session_store(request: Request) -> SessionStore:
    store = getattr(request.app.state, "session_store", None)
    if store is None:
        from backend import config

        store = SessionStore(
            db_path=config.MEMORY_DB_PATH,
            ttl_seconds=config.AUTH_SESSION_TTL_SECONDS,
        )
        request.app.state.session_store = store
    return store


def _extract_token(
    authorization: Optional[str],
    x_session_token: Optional[str],
) -> Optional[str]:
    if x_session_token and x_session_token.strip():
        return x_session_token.strip()
    if authorization:
        parts = authorization.strip().split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
            return parts[1].strip()
        if len(parts) == 1 and parts[0].strip():
            # bare token without Bearer prefix
            return parts[0].strip()
    return None


def require_session(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_session_token: Optional[str] = Header(default=None, alias="X-Session-Token"),
    store: SessionStore = Depends(get_session_store),
) -> SellerSession:
    """Require a valid opaque session (Authorization: Bearer or X-Session-Token)."""
    token = _extract_token(authorization, x_session_token)
    session = store.get(token)
    if session is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return session


def require_seller_match(
    seller_id: str,
    session: SellerSession = Depends(require_session),
) -> SellerSession:
    """
    Path may still contain {seller_id}; data access uses authenticated seller.
    Mismatch → 403 (do not trust client-supplied seller_id alone).
    """
    if str(seller_id) != str(session.seller_id):
        raise HTTPException(
            status_code=403,
            detail="seller_id does not match authenticated seller",
        )
    return session
