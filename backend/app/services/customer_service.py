"""
Customer CRM service.

- create: encrypts PII, computes blind indexes, refuses duplicates
  (per-org, on phone/email).
- merge: soft-deletes the source, moves touchpoints, fills missing PII on
  the target (ordering guarantees the partial unique indexes are happy).
- Every mutation writes an audit row (PII-redacted).
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TokenPayload
from app.core.encryption import canonical_email, canonical_phone, get_pii_engine
from app.models.customer import Customer
from app.models.touchpoint import Touchpoint
from app.schemas.customer import (
    CustomerCreate,
    CustomerOut,
    CustomerUpdate,
    TouchpointCreate,
    TouchpointOut,
)
from app.services import audit_service
from app.services.errors import ConflictError, DomainValidationError, NotFoundError


# ── Serialization / audit helpers ──────────────────────────


def serialize_customer(customer: Customer) -> CustomerOut:
    """Decrypt PII for API serialization (never used for logs)."""
    pii = get_pii_engine()
    return CustomerOut(
        id=customer.id,
        name=customer.name,
        email=pii.try_decrypt(customer.email_encrypted),
        phone=pii.try_decrypt(customer.phone_encrypted),
        company=customer.company,
        notes=customer.notes,
        tags=list(customer.tags or []),
        source=customer.source,
        merged_into_id=customer.merged_into_id,
        created_at=customer.created_at,
        updated_at=customer.updated_at,
    )


def serialize_touchpoint(touchpoint: Touchpoint) -> TouchpointOut:
    return TouchpointOut(
        id=touchpoint.id,
        customer_id=touchpoint.customer_id,
        kind=touchpoint.kind,
        summary=touchpoint.summary,
        ref_type=touchpoint.ref_type,
        ref_id=touchpoint.ref_id,
        meta=dict(touchpoint.meta or {}),
        occurred_at=touchpoint.occurred_at,
        created_by_id=touchpoint.created_by_id,
        created_at=touchpoint.created_at,
    )


def _audit_snapshot(customer: Customer) -> dict:
    """PII-free structural snapshot for the audit trail."""
    return {
        "name": customer.name,
        "company": customer.company,
        "source": customer.source,
        "tags": list(customer.tags or []),
        "has_email": customer.email_bidx is not None,
        "has_phone": customer.phone_bidx is not None,
        "merged_into_id": str(customer.merged_into_id) if customer.merged_into_id else None,
    }


# ── Lookups ────────────────────────────────────────────────


async def get_customer(
    session: AsyncSession, actor: TokenPayload, customer_id: uuid.UUID
) -> Customer:
    customer = await session.scalar(
        select(Customer).where(
            Customer.id == customer_id,
            Customer.org_id == actor.org_id,
            Customer.deleted_at.is_(None),
        )
    )
    if customer is None:
        raise NotFoundError("Customer not found")
    return customer


async def _assert_not_duplicate(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    email_bidx: str | None,
    phone_bidx: str | None,
    exclude_id: uuid.UUID | None = None,
) -> None:
    conditions = []
    if email_bidx:
        conditions.append(Customer.email_bidx == email_bidx)
    if phone_bidx:
        conditions.append(Customer.phone_bidx == phone_bidx)
    if not conditions:
        return
    stmt = select(Customer.id).where(
        Customer.org_id == org_id,
        Customer.deleted_at.is_(None),
        Customer.merged_into_id.is_(None),
        or_(*conditions),
    )
    if exclude_id is not None:
        stmt = stmt.where(Customer.id != exclude_id)
    existing_id = await session.scalar(stmt.limit(1))
    if existing_id is not None:
        raise ConflictError(
            "A customer with the same phone or email already exists",
            details={"existing_customer_id": str(existing_id)},
        )


# ── Writes ─────────────────────────────────────────────────


async def create_customer(
    session: AsyncSession, data: CustomerCreate, actor: TokenPayload
) -> Customer:
    pii = get_pii_engine()
    email = canonical_email(str(data.email)) if data.email else None
    phone = canonical_phone(data.phone) if data.phone else None
    email_bidx = pii.blind_index(email) if email else None
    phone_bidx = pii.blind_index(phone) if phone else None

    await _assert_not_duplicate(
        session, org_id=actor.org_id, email_bidx=email_bidx, phone_bidx=phone_bidx
    )

    customer = Customer(
        org_id=actor.org_id,
        name=data.name.strip(),
        email_encrypted=pii.encrypt(email),
        email_bidx=email_bidx,
        phone_encrypted=pii.encrypt(phone),
        phone_bidx=phone_bidx,
        company=data.company,
        notes=data.notes,
        tags=list(data.tags),
        source=data.source,
        created_by_id=uuid.UUID(actor.sub),
    )
    session.add(customer)
    try:
        await session.flush()
    except IntegrityError as exc:  # race with a concurrent create
        raise ConflictError(
            "A customer with the same phone or email already exists"
        ) from exc

    await audit_service.record(
        session,
        action="customer.created",
        entity_type="customer",
        entity_id=customer.id,
        after_state=_audit_snapshot(customer),
    )
    return customer


async def update_customer(
    session: AsyncSession,
    customer_id: uuid.UUID,
    data: CustomerUpdate,
    actor: TokenPayload,
) -> Customer:
    customer = await get_customer(session, actor, customer_id)
    before = _audit_snapshot(customer)
    payload = data.model_dump(exclude_unset=True)
    pii = get_pii_engine()

    new_email_bidx = customer.email_bidx
    new_phone_bidx = customer.phone_bidx
    new_email_enc = customer.email_encrypted
    new_phone_enc = customer.phone_encrypted

    if "email" in payload:
        email = canonical_email(str(payload["email"])) if payload["email"] else None
        new_email_bidx = pii.blind_index(email) if email else None
        new_email_enc = pii.encrypt(email)
    if "phone" in payload:
        phone = canonical_phone(payload["phone"]) if payload["phone"] else None
        new_phone_bidx = pii.blind_index(phone) if phone else None
        new_phone_enc = pii.encrypt(phone)

    if new_email_bidx != customer.email_bidx or new_phone_bidx != customer.phone_bidx:
        await _assert_not_duplicate(
            session,
            org_id=actor.org_id,
            email_bidx=new_email_bidx,
            phone_bidx=new_phone_bidx,
            exclude_id=customer.id,
        )

    customer.email_bidx = new_email_bidx
    customer.email_encrypted = new_email_enc
    customer.phone_bidx = new_phone_bidx
    customer.phone_encrypted = new_phone_enc
    if "name" in payload and payload["name"] is not None:
        customer.name = payload["name"].strip()
    for field in ("company", "notes", "tags"):
        if field in payload and payload[field] is not None:
            setattr(customer, field, payload[field])

    try:
        await session.flush()
    except IntegrityError as exc:
        raise ConflictError(
            "A customer with the same phone or email already exists"
        ) from exc
    # onupdate=now() expires updated_at after UPDATE; refresh explicitly.
    await session.refresh(customer)

    await audit_service.record(
        session,
        action="customer.updated",
        entity_type="customer",
        entity_id=customer.id,
        before_state=before,
        after_state=_audit_snapshot(customer),
    )
    return customer


async def delete_customer(
    session: AsyncSession, customer_id: uuid.UUID, actor: TokenPayload
) -> None:
    """Soft delete — 30-day retention before purge/crypto-shred."""
    customer = await get_customer(session, actor, customer_id)
    customer.deleted_at = datetime.now(timezone.utc)
    await session.flush()
    await audit_service.record(
        session,
        action="customer.deleted",
        entity_type="customer",
        entity_id=customer.id,
        before_state=_audit_snapshot(customer),
    )


async def merge_customers(
    session: AsyncSession,
    source_id: uuid.UUID,
    target_id: uuid.UUID,
    actor: TokenPayload,
) -> Customer:
    """Merge ``source`` into ``target``; the source becomes a soft-deleted,
    merged tombstone pointing at the target."""
    if source_id == target_id:
        raise DomainValidationError("Cannot merge a customer into itself")

    source = await get_customer(session, actor, source_id)
    target = await get_customer(session, actor, target_id)
    if target.merged_into_id is not None:
        raise DomainValidationError("Target customer is itself merged")

    # 1) Soft-delete the source first — frees its blind indexes from the
    #    partial unique constraints.
    source.deleted_at = datetime.now(timezone.utc)
    source.merged_into_id = target.id
    await session.flush()

    # 2) Fill missing PII on the target from the source.
    if target.email_bidx is None and source.email_bidx is not None:
        target.email_bidx = source.email_bidx
        target.email_encrypted = source.email_encrypted
    if target.phone_bidx is None and source.phone_bidx is not None:
        target.phone_bidx = source.phone_bidx
        target.phone_encrypted = source.phone_encrypted

    # 3) Re-point touchpoints.
    await session.execute(
        update(Touchpoint)
        .where(Touchpoint.customer_id == source.id)
        .values(customer_id = target.id)
    )
    await session.flush()
    # onupdate=now() expires updated_at after UPDATE; refresh explicitly.
    await session.refresh(target)

    await audit_service.record(
        session,
        action="customer.merged",
        entity_type="customer",
        entity_id=source.id,
        after_state=_audit_snapshot(source),
        meta={"source_id": str(source.id), "target_id": str(target.id)},
    )
    return target


# ── Queries ────────────────────────────────────────────────


async def list_customers(
    session: AsyncSession,
    actor: TokenPayload,
    *,
    q: str | None = None,
    limit: int = 20,
    offset: int = 0,
    include_merged: bool = False,
) -> tuple[list[Customer], int]:
    stmt = select(Customer).where(
        Customer.org_id == actor.org_id,
        Customer.deleted_at.is_(None),
    )
    if not include_merged:
        stmt = stmt.where(Customer.merged_into_id.is_(None))

    if q and q.strip():
        term = q.strip()
        pii = get_pii_engine()
        filters = [Customer.name.ilike(f"%{term}%")]
        email_bidx = pii.blind_index(canonical_email(term))
        if email_bidx:
            filters.append(Customer.email_bidx == email_bidx)
        phone_c = canonical_phone(term)
        if len(re.sub(r"\D", "", phone_c)) >= 5:
            filters.append(Customer.phone_bidx == pii.blind_index(phone_c))
        stmt = stmt.where(or_(*filters))

    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (
        await session.execute(
            stmt.order_by(Customer.created_at.desc()).limit(limit).offset(offset)
        )
    ).scalars().all()
    return list(rows), int(total)


# ── Touchpoints ────────────────────────────────────────────


async def add_touchpoint(
    session: AsyncSession,
    customer_id: uuid.UUID,
    data: TouchpointCreate,
    actor: TokenPayload,
) -> Touchpoint:
    customer = await get_customer(session, actor, customer_id)
    touchpoint = Touchpoint(
        org_id=actor.org_id,
        customer_id=customer.id,
        kind=data.kind,
        summary=data.summary,
        ref_type=data.ref_type,
        ref_id=data.ref_id,
        meta=data.meta,
        occurred_at=data.occurred_at or datetime.now(timezone.utc),
        created_by_id=uuid.UUID(actor.sub),
    )
    session.add(touchpoint)
    await session.flush()
    await audit_service.record(
        session,
        action="touchpoint.created",
        entity_type="touchpoint",
        entity_id=touchpoint.id,
        after_state={"customer_id": str(customer.id), "kind": data.kind},
    )
    return touchpoint


async def list_touchpoints(
    session: AsyncSession,
    customer_id: uuid.UUID,
    actor: TokenPayload,
    *,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Touchpoint], int]:
    await get_customer(session, actor, customer_id)
    stmt = select(Touchpoint).where(
        Touchpoint.org_id == actor.org_id,
        Touchpoint.customer_id == customer_id,
    )
    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (
        await session.execute(
            stmt.order_by(Touchpoint.occurred_at.desc()).limit(limit).offset(offset)
        )
    ).scalars().all()
    return list(rows), int(total)
