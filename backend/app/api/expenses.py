"""Expense tracking API (proposal-backed writes)."""

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
from app.schemas.expense import ExpenseCreate, ExpenseCreateOut, ExpenseOut
from app.services import expense_service

router = APIRouter(prefix="/expenses", tags=["expenses"])


@router.get(
    "",
    response_model=Page[ExpenseOut],
    dependencies=[Depends(require_permission("expenses:read"))],
)
async def list_expenses(
    status_filter: str | None = Query(None, alias="status", max_length=20),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> Page[ExpenseOut]:
    items, total = await expense_service.list_expenses(
        session, user, status=status_filter, limit=limit, offset=offset
    )
    return Page[ExpenseOut](
        items=items, total=total, limit=limit, offset=offset
    )


@router.post(
    "",
    response_model=ExpenseCreateOut,
    dependencies=[Depends(require_permission("expenses:write"))],
)
async def create_expense(
    data: ExpenseCreate,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> ExpenseCreateOut:
    """Stage an expense proposal. Approval posts the journal entry."""
    expense, created = await expense_service.create_expense(session, data, user)
    return ExpenseCreateOut(expense=expense, created=created)


@router.get(
    "/{expense_id}",
    response_model=ExpenseOut,
    dependencies=[Depends(require_permission("expenses:read"))],
)
async def get_expense(
    expense_id: uuid.UUID,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> ExpenseOut:
    return await expense_service.get_expense(session, user, expense_id)
