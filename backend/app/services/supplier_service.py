"""Supplier reference API backing payable expenses."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.supplier import SupplierCreate, SupplierOut
from app.services import ledger_service, posting_service
from app.services.errors import NotFoundError


def serialize_supplier(supplier: ledger_service.Supplier) -> SupplierOut:
    return SupplierOut(
        id=supplier.id,
        code=supplier.code,
        name=supplier.name,
        is_active=supplier.is_active,
    )


async def list_suppliers(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    q: str | None = None,
    include_inactive: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[SupplierOut], int]:
    rows = await ledger_service.list_suppliers(
        session, org_id, include_inactive=include_inactive
    )
    if q and q.strip():
        needle = q.strip().casefold()
        rows = [
            row
            for row in rows
            if needle in row.name.casefold()
            or (row.code is not None and needle in row.code.casefold())
        ]
    total = len(rows)
    return [serialize_supplier(row) for row in rows[offset : offset + limit]], total


async def get_supplier(
    session: AsyncSession, org_id: uuid.UUID, supplier_id: uuid.UUID
) -> SupplierOut:
    supplier = await ledger_service.get_supplier(session, org_id, supplier_id)
    if supplier is None:
        raise NotFoundError("Supplier not found")
    return serialize_supplier(supplier)


async def create_supplier(
    session: AsyncSession,
    org_id: uuid.UUID,
    data: SupplierCreate,
    *,
    user_id: uuid.UUID | None = None,
) -> SupplierOut:
    supplier_id = await posting_service.create_supplier(
        session,
        org_id,
        name=data.name.strip(),
        code=data.code.strip() if data.code else None,
        user_id=user_id,
    )
    supplier = await ledger_service.get_supplier(session, org_id, supplier_id)
    if supplier is None:
        raise NotFoundError("Supplier not found")
    return serialize_supplier(supplier)
