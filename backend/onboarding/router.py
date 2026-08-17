"""Onboarding HTTP routes — seller identity from session only."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.auth.deps import require_session
from backend.auth.session import SellerSession
from backend.onboarding.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    OnboardingStateResponse,
    ProductAddRequest,
    ProductAddResponse,
    WBCheckResponse,
    WBConnectRequest,
    WBConnectResponse,
    WBDisconnectResponse,
)
from backend.onboarding.service import OnboardingError, OnboardingService

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


def get_onboarding_service(request: Request) -> OnboardingService:
    svc = getattr(request.app.state, "onboarding_service", None)
    if svc is None:
        svc = OnboardingService()
        request.app.state.onboarding_service = svc
    return svc


def _raise(exc: OnboardingError) -> None:
    # Never put secrets into detail.
    raise HTTPException(
        status_code=exc.http_status,
        detail={"error": exc.code, "message": exc.message},
    )


@router.get("/state", response_model=OnboardingStateResponse)
async def onboarding_state(
    session: SellerSession = Depends(require_session),
    svc: OnboardingService = Depends(get_onboarding_service),
) -> OnboardingStateResponse:
    data = await svc.get_state(session)
    return OnboardingStateResponse(**data)


@router.post("/wb/connect", response_model=WBConnectResponse)
async def wb_connect(
    body: WBConnectRequest,
    session: SellerSession = Depends(require_session),
    svc: OnboardingService = Depends(get_onboarding_service),
) -> WBConnectResponse:
    try:
        data = await svc.connect_wb(session, body.api_key)
    except OnboardingError as exc:
        if exc.code == "invalid_credentials":
            return WBConnectResponse(
                connected=False,
                status=svc.ensure_from_session(session).onboarding_status.value,
                error="invalid_credentials",
            )
        _raise(exc)
    return WBConnectResponse(**data)


@router.post("/wb/check", response_model=WBCheckResponse)
async def wb_check(
    session: SellerSession = Depends(require_session),
    svc: OnboardingService = Depends(get_onboarding_service),
) -> WBCheckResponse:
    data = await svc.check_wb(session)
    return WBCheckResponse(**data)


@router.post("/wb/disconnect", response_model=WBDisconnectResponse)
async def wb_disconnect(
    session: SellerSession = Depends(require_session),
    svc: OnboardingService = Depends(get_onboarding_service),
) -> WBDisconnectResponse:
    data = await svc.disconnect_wb(session)
    return WBDisconnectResponse(**data)


@router.post("/product", response_model=ProductAddResponse)
async def onboarding_product(
    body: ProductAddRequest,
    session: SellerSession = Depends(require_session),
    svc: OnboardingService = Depends(get_onboarding_service),
) -> ProductAddResponse:
    try:
        data = await svc.add_first_product(session, body.article)
    except OnboardingError as exc:
        _raise(exc)
    return ProductAddResponse(**data)


@router.post("/analyze", response_model=AnalyzeResponse)
async def onboarding_analyze(
    body: AnalyzeRequest | None = None,
    session: SellerSession = Depends(require_session),
    svc: OnboardingService = Depends(get_onboarding_service),
) -> AnalyzeResponse:
    article = body.article if body else None
    try:
        data = await svc.first_analyze(session, article=article)
    except OnboardingError as exc:
        _raise(exc)
    return AnalyzeResponse(**data)
