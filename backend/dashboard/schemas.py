"""Pydantic schemas for Seller Dashboard."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

ArgusStatus = Literal["RED", "YELLOW", "GREEN"]
Priority = Literal["high", "medium", "low"]


class SellerInfo(BaseModel):
    id: str
    name: str
    subscription: str = "demo"


class DashboardMetrics(BaseModel):
    argus_index: int
    products_count: int
    problems_count: int
    opportunities_count: int


class DashboardProduct(BaseModel):
    article: int
    title: str
    price: Optional[int] = None
    rating: Optional[float] = None
    reviews_count: Optional[int] = None
    argus_status: ArgusStatus = "YELLOW"
    recommendations: list[str] = Field(default_factory=list)
    image: Optional[str] = None
    position: Optional[int] = None
    problems: list[str] = Field(default_factory=list)
    argus_score: Optional[int] = None


class CompetitorsBlock(BaseModel):
    top_products: list[dict[str, Any]] = Field(default_factory=list)
    market_position: str = "unknown"
    price_difference: float = 0.0


class DashboardAlert(BaseModel):
    type: str
    message: str
    priority: Priority = "medium"


class SellerDashboardResponse(BaseModel):
    seller: SellerInfo
    metrics: DashboardMetrics
    products: list[DashboardProduct] = Field(default_factory=list)
    competitors: CompetitorsBlock = Field(default_factory=CompetitorsBlock)
    alerts: list[DashboardAlert] = Field(default_factory=list)
    demo: bool = False
