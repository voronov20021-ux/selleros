"""Pydantic schemas for onboarding HTTP API — never include secrets."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class WBConnectRequest(BaseModel):
    api_key: str = Field(..., min_length=1)


class WBConnectResponse(BaseModel):
    connected: bool
    status: str
    error: Optional[str] = None


class WBCheckResponse(BaseModel):
    connected: bool
    status: str
    error: Optional[str] = None


class WBDisconnectResponse(BaseModel):
    connected: bool = False
    status: str
    revoked: bool = True


class ProductAddRequest(BaseModel):
    article: int = Field(..., gt=0)


class ProductAddResponse(BaseModel):
    article: int
    title: Optional[str] = None
    status: str
    already_existed: bool = False
    source: Optional[str] = None


class AnalyzeRequest(BaseModel):
    article: Optional[int] = Field(default=None, gt=0)


class AnalyzeResponse(BaseModel):
    article: int
    score: int
    verdict: str
    status: str
    already_analyzed: bool = False


class OnboardingStateResponse(BaseModel):
    seller_id: str
    display_name: str
    status: str
    wb_connected: bool
    has_product: bool
    has_analysis: bool
    first_article: Optional[int] = None
    steps: dict[str, Any]
