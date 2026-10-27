"""Customer CRM API."""

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
from app.schemas.customer import (
    CustomerCreate,
    CustomerMergeRequest,
    CustomerOut,
    CustomerUpdate,
    TouchpointCreate,
    TouchpointOut,
)
from app.services import customer_service

router = APIRouter(prefix="/customers", tags=["customers"])


@router.get(
    "",
    response_model=Page[CustomerOut],
    dependencies=[Depends(require_permission("customers:read"))],
)
async def list_customers(
    q: str | None = Query(None, max_length=200, description="Name / email / phone search"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    include_merged: bool = False,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> Page[CustomerOut]:
    rows, total = await customer_service.list_customers(
        session, user, q=q, limit=limit, offset=offset, include_merged=include_merged
    )
    return Page[CustomerOut](
        items=[customer_service.serialize_customer(c) for c in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    response_model=CustomerOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("customers:write"))],
)
async def create_customer(
    data: CustomerCreate,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> CustomerOut:
    """Rejects duplicates on phone/email (409 with the existing customer id)."""
    customer = await customer_service.create_customer(session, data, user)
    return customer_service.serialize_customer(customer)


@router.get(
    "/{customer_id}",
    response_model=CustomerOut,
    dependencies=[Depends(require_permission("customers:read"))],
)
async def get_customer(
    customer_id: uuid.UUID,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> CustomerOut:
    customer = await customer_service.get_customer(session, user, customer_id)
    return customer_service.serialize_customer(customer)


@router.patch(
    "/{customer_id}",
    response_model=CustomerOut,
    dependencies=[Depends(require_permission("customers:write"))],
)
async def update_customer(
    customer_id: uuid.UUID,
    data: CustomerUpdate,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> CustomerOut:
    customer = await customer_service.update_customer(session, customer_id, data, user)
    return customer_service.serialize_customer(customer)


@router.delete(
    "/{customer_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("customers:delete"))],
)
async def delete_customer(
    customer_id: uuid.UUID,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> None:
    """Soft delete (30-day retention before purge/crypto-shred)."""
    await customer_service.delete_customer(session, customer_id, user)


@router.post(
    "/{customer_id}/merge",
    response_model=CustomerOut,
    dependencies=[Depends(require_permission("customers:write"))],
)
async def merge_customer(
    customer_id: uuid.UUID,
    data: CustomerMergeRequest,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> CustomerOut:
    """Merge the path customer (source) into ``target_id``."""
    target = await customer_service.merge_customers(
        session, customer_id, data.target_id, user
    )
    return customer_service.serialize_customer(target)


@router.get(
    "/{customer_id}/touchpoints",
    response_model=Page[TouchpointOut],
    dependencies=[Depends(require_permission("customers:read"))],
)
async def list_touchpoints(
    customer_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> Page[TouchpointOut]:
    rows, total = await customer_service.list_touchpoints(
        session, customer_id, user, limit=limit, offset=offset
    )
    return Page[TouchpointOut](
        items=[customer_service.serialize_touchpoint(t) for t in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/{customer_id}/touchpoints",
    response_model=TouchpointOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("customers:write"))],
)
async def create_touchpoint(
    customer_id: uuid.UUID,
    data: TouchpointCreate,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> TouchpointOut:
    touchpoint = await customer_service.add_touchpoint(session, customer_id, data, user)
    return customer_service.serialize_touchpoint(touchpoint)
