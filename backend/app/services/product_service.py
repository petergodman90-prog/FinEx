"""Product master service backed by the posting-service setup functions."""

from __future__ import annotations

import uuid
from typing import NoReturn

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.product import ProductCreate, ProductOut, ProductUpdate
from app.services import ledger_service, posting_service
from app.services.errors import ConflictError, DomainValidationError, NotFoundError
from app.services.posting_service import PostingError


def serialize_product(product: ledger_service.Product) -> ProductOut:
    return ProductOut(
        id=product.id,
        sku=product.sku,
        name=product.name,
        unit=product.unit,
        current_sale_price=product.current_sale_price,
        current_cost=product.current_cost,
        reorder_point=product.reorder_point,
        is_active=product.is_active,
    )


def _raise_setup_error(exc: PostingError) -> NoReturn:
    if exc.code == "duplicate_posting":
        raise ConflictError(
            "A product with this SKU already exists",
            details=exc.details,
        ) from exc
    raise DomainValidationError(str(exc), details=exc.details) from exc


async def list_products(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    q: str | None = None,
    include_inactive: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[ProductOut], int]:
    rows, total = await ledger_service.list_products(
        session,
        org_id,
        include_inactive=include_inactive,
        search=q,
        limit=limit,
        offset=offset,
    )
    return [serialize_product(row) for row in rows], total


async def get_product(
    session: AsyncSession, org_id: uuid.UUID, product_id: uuid.UUID
) -> ProductOut:
    product = await ledger_service.get_product(session, org_id, product_id)
    if product is None:
        raise NotFoundError("Product not found")
    return serialize_product(product)


async def create_product(
    session: AsyncSession,
    org_id: uuid.UUID,
    data: ProductCreate,
    *,
    user_id: uuid.UUID | None = None,
) -> ProductOut:
    try:
        product_id = await posting_service.create_product(
            session,
            org_id,
            sku=data.sku,
            name=data.name,
            unit=data.unit,
            sale_price=data.sale_price,
            cost=data.cost,
            reorder_point=data.reorder_point,
            user_id=user_id,
        )
    except PostingError as exc:
        _raise_setup_error(exc)
    product = await ledger_service.get_product(session, org_id, product_id)
    if product is None:
        raise NotFoundError("Product not found")
    return serialize_product(product)


async def update_product(
    session: AsyncSession,
    org_id: uuid.UUID,
    product_id: uuid.UUID,
    data: ProductUpdate,
    *,
    user_id: uuid.UUID | None = None,
) -> ProductOut:
    existing = await ledger_service.get_product(session, org_id, product_id)
    if existing is None:
        raise NotFoundError("Product not found")
    try:
        await posting_service.update_product(
            session,
            org_id,
            product_id,
            sale_price=data.sale_price,
            cost=data.cost,
            reorder_point=data.reorder_point,
            is_active=data.is_active,
            user_id=user_id,
        )
    except PostingError as exc:
        _raise_setup_error(exc)
    product = await ledger_service.get_product(session, org_id, product_id)
    if product is None:
        raise NotFoundError("Product not found")
    return serialize_product(product)
