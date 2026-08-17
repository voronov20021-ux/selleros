"""FastAPI routes for Seller Dashboard.

All data endpoints require an authenticated Mini App session.
Path ``{seller_id}`` must match the authenticated seller (else 403);
data is always loaded for the session seller_id, never from a bare body field.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from backend.auth.deps import require_seller_match
from backend.auth.session import SellerSession
from backend.dashboard.schemas import SellerDashboardResponse
from backend.dashboard.service import DashboardService

router = APIRouter(tags=["dashboard"])


def _svc(request: Request) -> DashboardService:
    svc = getattr(request.app.state, "dashboard_service", None)
    if svc is None:
        svc = DashboardService(force_demo=True)
        request.app.state.dashboard_service = svc
    return svc


@router.get("/dashboard/{seller_id}", response_model=SellerDashboardResponse)
async def get_dashboard(
    seller_id: str,
    request: Request,
    session: SellerSession = Depends(require_seller_match),
) -> SellerDashboardResponse:
    """Primary MVP endpoint: seller dashboard payload (auth seller only)."""
    return await _svc(request).get_seller_dashboard(session.seller_id)


@router.get("/dashboard/{seller_id}/products")
async def get_dashboard_products(
    seller_id: str,
    request: Request,
    session: SellerSession = Depends(require_seller_match),
    filter: str = Query(default="all"),
):
    products = _svc(request).filter_products(session.seller_id, filter)
    return {"seller_id": session.seller_id, "filter": filter, "products": products}


@router.post("/dashboard/{seller_id}/products/{article}/actions/{action}")
async def product_action(
    seller_id: str,
    article: int,
    action: str,
    session: SellerSession = Depends(require_seller_match),
):
    """No WB mutations — TODO / confirmation only."""
    return {
        "status": "todo",
        "seller_id": session.seller_id,
        "article": article,
        "action": action,
        "draft": {
            "kind": action,
            "preview": f"Черновик «{action}» для {article}",
            "requires_confirmation": True,
            "wb_publish": False,
        },
        "message": "TODO: подтверждение. Автопубликация на Wildberries отключена.",
    }
