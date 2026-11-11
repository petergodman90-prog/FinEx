"""Debts, aging, and settlement API."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TokenPayload
from app.core.dependencies import (
    get_current_user,
    get_rls_session,
    require_permission,
)
from app.schemas.common import Page
from app.schemas.debt import (
    DebtAgingOut,
    DebtOut,
    DebtSummaryOut,
    SettlementCreate,
    SettlementOut,
    SettlementProposalOut,
)
from app.services import debt_service

router = APIRouter(prefix="/debts", tags=["debts"])

DebtItemType = Literal["customer_receivable", "supplier_payable"]


@router.get(
    "",
    response_model=Page[DebtOut],
    dependencies=[Depends(require_permission("debts:read"))],
)
async def list_debts(
    item_type: DebtItemType | None = None,
    outstanding_only: bool = True,
    overdue_only: bool = False,
    counterparty_id: uuid.UUID | None = None,
    aging_bucket: str | None = Query(None, max_length=20),
    q: str | None = Query(None, max_length=200),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> Page[DebtOut]:
    items, total = await debt_service.list_debts(
        session,
        user.org_id,
        item_type=item_type,
        outstanding_only=outstanding_only,
        overdue_only=overdue_only,
        counterparty_id=counterparty_id,
        aging_bucket=aging_bucket,
        q=q,
        limit=limit,
        offset=offset,
    )
    return Page[DebtOut](
        items=items, total=total, limit=limit, offset=offset
    )


@router.get(
    "/summary",
    response_model=DebtSummaryOut,
    dependencies=[Depends(require_permission("debts:read"))],
)
async def get_debt_summary(
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> DebtSummaryOut:
    return await debt_service.get_summary(session, user.org_id)


@router.get(
    "/aging",
    response_model=DebtAgingOut,
    dependencies=[Depends(require_permission("debts:read"))],
)
async def get_debt_aging(
    item_type: DebtItemType = Query(...),
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> DebtAgingOut:
    return await debt_service.get_aging(session, user.org_id, item_type)


@router.get(
    "/{debt_id}",
    response_model=DebtOut,
    dependencies=[Depends(require_permission("debts:read"))],
)
async def get_debt(
    debt_id: uuid.UUID,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> DebtOut:
    return await debt_service.get_debt(session, user.org_id, debt_id)


@router.get(
    "/{debt_id}/settlements",
    response_model=Page[SettlementOut],
    dependencies=[Depends(require_permission("debts:read"))],
)
async def list_settlements(
    debt_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> Page[SettlementOut]:
    items, total = await debt_service.list_settlements(
        session, user.org_id, debt_id, limit=limit, offset=offset
    )
    return Page[SettlementOut](
        items=items, total=total, limit=limit, offset=offset
    )


@router.post(
    "/{debt_id}/settlements",
    response_model=SettlementProposalOut,
    dependencies=[Depends(require_permission("debts:write"))],
)
async def create_settlement(
    debt_id: uuid.UUID,
    data: SettlementCreate,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> SettlementProposalOut:
    """Stage a settlement proposal. Approval posts the payment allocation."""
    return await debt_service.create_settlement(
        session, user.org_id, debt_id, data, user
    )
