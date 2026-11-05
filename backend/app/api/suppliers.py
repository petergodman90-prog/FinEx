"""Supplier reference API."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TokenPayload
from app.core.dependencies import (
    get_current_user,
    get_rls_session,
    require_permission,
)
from app.schemas.common import Page
from app.schemas.supplier import SupplierCreate, SupplierOut
from app.services import supplier_service

router = APIRouter(prefix="/suppliers", tags=["suppliers"])


@router.get(
    "",
    response_model=Page[SupplierOut],
    dependencies=[Depends(require_permission("suppliers:read"))],
)
async def list_suppliers(
    q: str | None = Query(None, max_length=200),
    include_inactive: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> Page[SupplierOut]:
    items, total = await supplier_service.list_suppliers(
        session,
        user.org_id,
        q=q,
        include_inactive=include_inactive,
        limit=limit,
        offset=offset,
    )
    return Page[SupplierOut](
        items=items, total=total, limit=limit, offset=offset
    )


@router.post(
    "",
    response_model=SupplierOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("suppliers:write"))],
)
async def create_supplier(
    data: SupplierCreate,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> SupplierOut:
    return await supplier_service.create_supplier(
        session, user.org_id, data, user_id=uuid.UUID(user.sub)
    )


@router.get(
    "/{supplier_id}",
    response_model=SupplierOut,
    dependencies=[Depends(require_permission("suppliers:read"))],
)
async def get_supplier(
    supplier_id: uuid.UUID,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> SupplierOut:
    return await supplier_service.get_supplier(session, user.org_id, supplier_id)
