"""Dashboard API — aggregation metrics for the frontend."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TokenPayload
from app.core.dependencies import (
    get_current_user,
    get_rls_session,
    require_permission,
)
from app.schemas.dashboard import DashboardMetrics
from app.services import dashboard_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get(
    "/metrics",
    response_model=DashboardMetrics,
    dependencies=[Depends(require_permission("dashboard:read"))],
)
async def get_metrics(
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> DashboardMetrics:
    metrics = await dashboard_service.get_metrics(session, user)
    return DashboardMetrics(**metrics)
