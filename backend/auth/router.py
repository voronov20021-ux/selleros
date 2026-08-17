"""Auth HTTP routes — Telegram Mini App login / logout."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Request
from pydantic import BaseModel, Field

from backend.auth.deps import _extract_token, get_session_store, require_session
from backend.auth.dev_preview import (
    DEV_PREVIEW_DISPLAY_NAME,
    DEV_PREVIEW_SELLER_ID,
    DEV_PREVIEW_USERNAME,
    miniapp_dev_auth_allowed,
)
from backend.auth.session import SellerSession, SessionStore
from backend.auth.telegram_webapp import TelegramAuthError, validate_init_data
from backend import config

router = APIRouter(prefix="/api/auth", tags=["auth"])


class TelegramAuthRequest(BaseModel):
    initData: str = Field(..., min_length=1, description="Telegram WebApp initData")


class TelegramAuthResponse(BaseModel):
    session_token: str
    seller_id: str
    telegram_user_id: str
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    display_name: str
    is_new_seller: bool = False


class LogoutResponse(BaseModel):
    ok: bool = True


@router.post("/telegram", response_model=TelegramAuthResponse)
async def auth_telegram(
    body: TelegramAuthRequest,
    request: Request,
    store: SessionStore = Depends(get_session_store),
) -> TelegramAuthResponse:
    """
    Validate Telegram WebApp initData and issue an opaque session token.

    seller_id = telegram_user_id (string). First login creates seller identity
    implicitly (is_new_seller=true); re-auth returns a fresh session for the
    same seller (is_new_seller=false).
    """
    try:
        user = validate_init_data(
            body.initData,
            config.BOT_TOKEN,
            max_age_seconds=config.TELEGRAM_AUTH_MAX_AGE,
        )
    except TelegramAuthError as exc:
        status = 401
        if exc.code in ("missing", "malformed"):
            status = 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc

    session = store.create_session(
        telegram_user_id=user.telegram_user_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
    )

    return TelegramAuthResponse(
        session_token=session.token,
        seller_id=session.seller_id,
        telegram_user_id=session.telegram_user_id,
        username=session.username,
        first_name=session.first_name,
        last_name=session.last_name,
        display_name=session.display_name,
        is_new_seller=session.is_new_seller,
    )


@router.post("/dev", response_model=TelegramAuthResponse)
async def auth_dev(
    request: Request,
    store: SessionStore = Depends(get_session_store),
) -> TelegramAuthResponse:
    """Synthetic local preview session. Not a Telegram HMAC bypass.

    Succeeds only when MINIAPP_DEV_AUTH is on AND the request is loopback
    AND APP_ENV is not production. Missing initData never falls through here.
    """
    ok, reason = miniapp_dev_auth_allowed(request)
    if not ok:
        if reason == "disabled":
            raise HTTPException(status_code=404, detail="Not found")
        raise HTTPException(status_code=403, detail="DEV auth is not available")

    session = store.create_session(
        telegram_user_id=DEV_PREVIEW_SELLER_ID,
        username=DEV_PREVIEW_USERNAME,
        first_name=DEV_PREVIEW_DISPLAY_NAME,
        last_name=None,
    )
    return TelegramAuthResponse(
        session_token=session.token,
        seller_id=session.seller_id,
        telegram_user_id=session.telegram_user_id,
        username=session.username,
        first_name=session.first_name,
        last_name=session.last_name,
        display_name=DEV_PREVIEW_DISPLAY_NAME,
        is_new_seller=session.is_new_seller,
    )


@router.post("/logout", response_model=LogoutResponse)
async def auth_logout(
    authorization: Optional[str] = Header(default=None),
    x_session_token: Optional[str] = Header(default=None, alias="X-Session-Token"),
    session: SellerSession = Depends(require_session),
    store: SessionStore = Depends(get_session_store),
) -> LogoutResponse:
    """Revoke the current opaque session. Subsequent dashboard calls → 401."""
    token = _extract_token(authorization, x_session_token)
    store.revoke_session(token)
    return LogoutResponse(ok=True)
