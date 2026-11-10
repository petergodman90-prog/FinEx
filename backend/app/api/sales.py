"""Sales and credit-note API (proposal-backed writes)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TokenPayload
from app.core.dependencies import (
    get_current_user,
    get_rls_session,
    require_permission,
)
from app.schemas.common import Page
from app.schemas.sale import (
    CreditNoteCreate,
    CreditNoteCreateOut,
    CreditNoteOut,
    SaleCreate,
    SaleCreateOut,
    SaleOut,
)
from app.services import sale_service

router = APIRouter(prefix="/sales", tags=["sales"])


@router.get(
    "",
    response_model=Page[SaleOut],
    dependencies=[Depends(require_permission("sales:read"))],
)
async def list_sales(
    status_filter: str | None = Query(None, alias="status", max_length=20),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> Page[SaleOut]:
    items, total = await sale_service.list_sales(
        session, user, status=status_filter, limit=limit, offset=offset
    )
    return Page[SaleOut](
        items=items, total=total, limit=limit, offset=offset
    )


@router.post(
    "",
    response_model=SaleCreateOut,
    dependencies=[Depends(require_permission("sales:write"))],
)
async def create_sale(
    data: SaleCreate,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> SaleCreateOut:
    """Stage a sale proposal. Approval posts revenue, COGS, stock and receipts."""
    return await sale_service.create_sale(session, data, user)


@router.get(
    "/credit-notes",
    response_model=Page[CreditNoteOut],
    dependencies=[Depends(require_permission("sales:read"))],
)
async def list_credit_notes(
    status_filter: str | None = Query(None, alias="status", max_length=20),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> Page[CreditNoteOut]:
    items, total = await sale_service.list_credit_notes(
        session, user, status=status_filter, limit=limit, offset=offset
    )
    return Page[CreditNoteOut](
        items=items, total=total, limit=limit, offset=offset
    )


@router.post(
    "/credit-notes",
    response_model=CreditNoteCreateOut,
    dependencies=[Depends(require_permission("sales:write"))],
)
async def create_credit_note(
    data: CreditNoteCreate,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> CreditNoteCreateOut:
    """Stage a credit-note proposal. Approval posts the refund and restock."""
    return await sale_service.create_credit_note(session, data, user)


@router.get(
    "/credit-notes/{credit_note_id}",
    response_model=CreditNoteOut,
    dependencies=[Depends(require_permission("sales:read"))],
)
async def get_credit_note(
    credit_note_id: uuid.UUID,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> CreditNoteOut:
    return await sale_service.get_credit_note(session, user, credit_note_id)


@router.get(
    "/{sale_id}",
    response_model=SaleOut,
    dependencies=[Depends(require_permission("sales:read"))],
)
async def get_sale(
    sale_id: uuid.UUID,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> SaleOut:
    return await sale_service.get_sale(session, user, sale_id)
