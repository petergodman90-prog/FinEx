"""Inventory stock, opening stock, and stock adjustment API."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TokenPayload
from app.core.dependencies import (
    get_current_user,
    get_rls_session,
    require_permission,
)
from app.schemas.common import Page
from app.schemas.inventory import (
    OpeningStockCreate,
    StockAdjustmentCreate,
    StockLevelOut,
    StockMovementOut,
    StockMovementType,
    StockProposalOut,
    StockValuationOut,
)
from app.schemas.proposal import ProposalOut
from app.services import inventory_service

router = APIRouter(prefix="/inventory", tags=["inventory"])

OPENING_STOCK = "opening_stock"
STOCK_ADJUSTMENT = "stock_adjustment"


@router.get(
    "/stock-levels",
    response_model=Page[StockLevelOut],
    dependencies=[Depends(require_permission("inventory:read"))],
)
async def list_stock_levels(
    q: str | None = Query(None, max_length=200),
    include_inactive: bool = False,
    needs_reorder: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> Page[StockLevelOut]:
    items, total = await inventory_service.list_stock_levels(
        session,
        user.org_id,
        q=q,
        include_inactive=include_inactive,
        needs_reorder=needs_reorder,
        limit=limit,
        offset=offset,
    )
    return Page[StockLevelOut](
        items=items, total=total, limit=limit, offset=offset
    )


@router.get(
    "/stock-levels/{product_id}",
    response_model=StockLevelOut,
    dependencies=[Depends(require_permission("inventory:read"))],
)
async def get_stock_level(
    product_id: uuid.UUID,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> StockLevelOut:
    return await inventory_service.get_stock_level(
        session, user.org_id, product_id
    )


@router.get(
    "/valuation",
    response_model=StockValuationOut,
    dependencies=[Depends(require_permission("inventory:read"))],
)
async def get_stock_valuation(
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> StockValuationOut:
    return await inventory_service.get_stock_valuation(session, user.org_id)


@router.get(
    "/movements",
    response_model=Page[StockMovementOut],
    dependencies=[Depends(require_permission("inventory:read"))],
)
async def list_stock_movements(
    product_id: uuid.UUID | None = None,
    movement_type: StockMovementType | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> Page[StockMovementOut]:
    items, total = await inventory_service.list_stock_movements(
        session,
        user.org_id,
        product_id=product_id,
        movement_type=movement_type,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return Page[StockMovementOut](
        items=items, total=total, limit=limit, offset=offset
    )


@router.get(
    "/opening-stock",
    response_model=Page[ProposalOut],
    dependencies=[Depends(require_permission("inventory:read"))],
)
async def list_opening_stock_proposals(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> Page[ProposalOut]:
    items, total = await inventory_service.list_stock_proposals(
        session,
        user,
        proposal_type=OPENING_STOCK,
        limit=limit,
        offset=offset,
    )
    return Page[ProposalOut](
        items=items, total=total, limit=limit, offset=offset
    )


@router.post(
    "/opening-stock",
    response_model=StockProposalOut,
    dependencies=[Depends(require_permission("inventory:write"))],
)
async def create_opening_stock(
    data: OpeningStockCreate,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> StockProposalOut:
    return await inventory_service.create_opening_stock(session, data, user)


@router.get(
    "/opening-stock/{proposal_id}",
    response_model=ProposalOut,
    dependencies=[Depends(require_permission("inventory:read"))],
)
async def get_opening_stock_proposal(
    proposal_id: uuid.UUID,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> ProposalOut:
    return await inventory_service.get_stock_proposal(
        session,
        user,
        proposal_id,
        proposal_type=OPENING_STOCK,
    )


@router.get(
    "/adjustments",
    response_model=Page[ProposalOut],
    dependencies=[Depends(require_permission("inventory:read"))],
)
async def list_stock_adjustment_proposals(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> Page[ProposalOut]:
    items, total = await inventory_service.list_stock_proposals(
        session,
        user,
        proposal_type=STOCK_ADJUSTMENT,
        limit=limit,
        offset=offset,
    )
    return Page[ProposalOut](
        items=items, total=total, limit=limit, offset=offset
    )


@router.post(
    "/adjustments",
    response_model=StockProposalOut,
    dependencies=[Depends(require_permission("inventory:write"))],
)
async def create_stock_adjustment(
    data: StockAdjustmentCreate,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> StockProposalOut:
    return await inventory_service.create_stock_adjustment(session, data, user)


@router.get(
    "/adjustments/{proposal_id}",
    response_model=ProposalOut,
    dependencies=[Depends(require_permission("inventory:read"))],
)
async def get_stock_adjustment_proposal(
    proposal_id: uuid.UUID,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> ProposalOut:
    return await inventory_service.get_stock_proposal(
        session,
        user,
        proposal_id,
        proposal_type=STOCK_ADJUSTMENT,
    )
