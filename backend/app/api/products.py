"""Product master API."""

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
from app.schemas.product import ProductCreate, ProductOut, ProductUpdate
from app.services import product_service

router = APIRouter(prefix="/products", tags=["products"])


@router.get(
    "",
    response_model=Page[ProductOut],
    dependencies=[Depends(require_permission("inventory:read"))],
)
async def list_products(
    q: str | None = Query(None, max_length=200),
    include_inactive: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> Page[ProductOut]:
    items, total = await product_service.list_products(
        session,
        user.org_id,
        q=q,
        include_inactive=include_inactive,
        limit=limit,
        offset=offset,
    )
    return Page[ProductOut](
        items=items, total=total, limit=limit, offset=offset
    )


@router.post(
    "",
    response_model=ProductOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("inventory:write"))],
)
async def create_product(
    data: ProductCreate,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> ProductOut:
    return await product_service.create_product(
        session, user.org_id, data, user_id=uuid.UUID(user.sub)
    )


@router.get(
    "/{product_id}",
    response_model=ProductOut,
    dependencies=[Depends(require_permission("inventory:read"))],
)
async def get_product(
    product_id: uuid.UUID,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> ProductOut:
    return await product_service.get_product(session, user.org_id, product_id)


@router.patch(
    "/{product_id}",
    response_model=ProductOut,
    dependencies=[Depends(require_permission("inventory:write"))],
)
async def update_product(
    product_id: uuid.UUID,
    data: ProductUpdate,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> ProductOut:
    return await product_service.update_product(
        session,
        user.org_id,
        product_id,
        data,
        user_id=uuid.UUID(user.sub),
    )
