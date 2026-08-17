"""Seller Dashboard API facade."""

from backend.dashboard.router import router
from backend.dashboard.service import DashboardService

__all__ = ["router", "DashboardService"]
