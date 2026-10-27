"""
Proposal service — staged writes awaiting human approval.

Safety rules (security charter §3):
- Idempotency: identical pending proposals collapse into one (hash per org).
- Expiry: default 24h, hard cap 72h; expired proposals can no longer be
  decided and are marked by the sweep.
- Role allow-list: enforced at the API layer (proposals:approve).
- Spend cap: per-role approval limit checked here on approval.
- Every create/decision/expiry writes an audit row.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TokenPayload
from app.core.rbac import SPEND_CAPS, Role, check_spend_cap
from app.models.proposal import Proposal, ProposalStatus
from app.schemas.proposal import ProposalCreate, ProposalOut
from app.services import audit_service
from app.services.errors import ConflictError, NotFoundError, PermissionDeniedError


# ── Helpers ────────────────────────────────────────────────


def compute_idempotency_hash(
    *,
    org_id: uuid.UUID,
    proposal_type: str,
    payload: dict,
    amount: Decimal | None,
    source: str,
    idempotency_key: str | None = None,
) -> str:
    canonical = json.dumps(
        {
            "org": str(org_id),
            "type": proposal_type,
            "payload": payload,
            "amount": str(amount) if amount is not None else None,
            "source": source,
            "key": idempotency_key,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def serialize_proposal(proposal: Proposal) -> ProposalOut:
    return ProposalOut(
        id=proposal.id,
        proposal_type=proposal.proposal_type,
        title=proposal.title,
        payload=dict(proposal.payload or {}),
        amount=proposal.amount,
        currency=proposal.currency,
        source=proposal.source,
        status=proposal.status,
        expires_at=proposal.expires_at,
        created_by_id=proposal.created_by_id,
        decided_by_id=proposal.decided_by_id,
        decided_at=proposal.decided_at,
        decision_note=proposal.decision_note,
        created_at=proposal.created_at,
        updated_at=proposal.updated_at,
    )


def _audit_snapshot(proposal: Proposal) -> dict:
    return {
        "proposal_type": proposal.proposal_type,
        "title": proposal.title,
        "status": proposal.status,
        "source": proposal.source,
        "amount": str(proposal.amount) if proposal.amount is not None else None,
        "currency": proposal.currency,
        "expires_at": proposal.expires_at.isoformat(),
    }


async def _find_pending(
    session: AsyncSession, org_id: uuid.UUID, idempotency_hash: str
) -> Proposal | None:
    return await session.scalar(
        select(Proposal).where(
            Proposal.org_id == org_id,
            Proposal.idempotency_hash == idempotency_hash,
            Proposal.status == ProposalStatus.PENDING.value,
            Proposal.deleted_at.is_(None),
        )
    )


async def get_proposal(
    session: AsyncSession, actor: TokenPayload, proposal_id: uuid.UUID
) -> Proposal:
    proposal = await session.scalar(
        select(Proposal).where(
            Proposal.id == proposal_id,
            Proposal.org_id == actor.org_id,
            Proposal.deleted_at.is_(None),
        )
    )
    if proposal is None:
        raise NotFoundError("Proposal not found")
    return proposal


# ── Writes ─────────────────────────────────────────────────


async def create_proposal(
    session: AsyncSession, data: ProposalCreate, actor: TokenPayload
) -> tuple[Proposal, bool]:
    """Create a proposal. Returns ``(proposal, created)``; ``created=False``
    means an identical pending proposal already existed (idempotent replay)."""
    org_id = actor.org_id
    idem = compute_idempotency_hash(
        org_id=org_id,
        proposal_type=data.proposal_type,
        payload=data.payload,
        amount=data.amount,
        source=data.source,
        idempotency_key=data.idempotency_key,
    )

    existing = await _find_pending(session, org_id, idem)
    if existing is not None:
        return existing, False

    proposal = Proposal(
        org_id=org_id,
        proposal_type=data.proposal_type,
        title=data.title.strip(),
        payload=data.payload,
        amount=data.amount,
        currency=data.currency,
        source=data.source,
        status=ProposalStatus.PENDING.value,
        idempotency_hash=idem,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=data.expires_in_hours),
        created_by_id=uuid.UUID(actor.sub),
    )
    try:
        # Savepoint: a concurrent replay trips the partial unique index and
        # rolls back only this insert, leaving the outer transaction usable.
        async with session.begin_nested():
            session.add(proposal)
            await session.flush()
    except IntegrityError:
        existing = await _find_pending(session, org_id, idem)
        if existing is None:
            raise ConflictError("Proposal conflicts with an existing record")
        return existing, False

    await audit_service.record(
        session,
        action="proposal.created",
        entity_type="proposal",
        entity_id=proposal.id,
        after_state=_audit_snapshot(proposal),
    )
    return proposal, True


async def _decide(
    session: AsyncSession,
    proposal_id: uuid.UUID,
    actor: TokenPayload,
    *,
    approve: bool,
    note: str | None,
) -> Proposal:
    # Lock the row for the whole decision so two concurrent approvals cannot
    # both observe "pending" and overwrite each other's decision/audit trail.
    proposal = await session.scalar(
        select(Proposal)
        .where(
            Proposal.id == proposal_id,
            Proposal.org_id == actor.org_id,
            Proposal.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if proposal is None:
        raise NotFoundError("Proposal not found")
    if proposal.status != ProposalStatus.PENDING.value:
        raise ConflictError(
            f"Proposal is '{proposal.status}' and can no longer be decided"
        )
    if proposal.expires_at <= datetime.now(timezone.utc):
        raise ConflictError(
            "Proposal has expired and can no longer be decided"
        )

    if approve:
        # A missing amount must never be treated as zero: otherwise a capped
        # approver could approve an arbitrarily large posting payload. Only an
        # unlimited approver may decide an unquantified proposal.
        approval_amount = (
            proposal.amount
            if proposal.amount is not None
            else Decimal("Infinity")
        )
        if not check_spend_cap(actor.role, approval_amount):
            try:
                cap = SPEND_CAPS[Role(actor.role)]
            except (ValueError, KeyError):
                cap = Decimal("0")
            raise PermissionDeniedError(
                (
                    "Proposal amount is missing; only an unlimited approver can "
                    "approve it"
                    if proposal.amount is None
                    else "Amount exceeds your role's approval limit"
                ),
                details={
                    "spend_cap": str(cap) if cap is not None else None,
                    "amount": (
                        str(proposal.amount)
                        if proposal.amount is not None
                        else None
                    ),
                },
            )

    proposal.status = (
        ProposalStatus.APPROVED.value if approve else ProposalStatus.REJECTED.value
    )
    proposal.decided_by_id = uuid.UUID(actor.sub)
    proposal.decided_at = datetime.now(timezone.utc)
    proposal.decision_note = note
    await session.flush()
    # onupdate=now() expires updated_at after UPDATE; explicit refresh avoids
    # a sync lazy-load (MissingGreenlet) during response serialization.
    await session.refresh(proposal)

    await audit_service.record(
        session,
        action=f"proposal.{proposal.status}",
        entity_type="proposal",
        entity_id=proposal.id,
        after_state=_audit_snapshot(proposal),
        meta={"note": note},
    )
    return proposal


async def approve_proposal(
    session: AsyncSession,
    proposal_id: uuid.UUID,
    actor: TokenPayload,
    note: str | None = None,
) -> Proposal:
    return await _decide(session, proposal_id, actor, approve=True, note=note)


async def reject_proposal(
    session: AsyncSession,
    proposal_id: uuid.UUID,
    actor: TokenPayload,
    note: str | None = None,
) -> Proposal:
    return await _decide(session, proposal_id, actor, approve=False, note=note)


async def expire_pending(session: AsyncSession, actor: TokenPayload) -> list[str]:
    """Mark all pending-but-expired proposals as expired. Returns their ids."""
    now = datetime.now(timezone.utc)
    ids = (
        (
            await session.execute(
                update(Proposal)
                .where(
                    Proposal.org_id == actor.org_id,
                    Proposal.status == ProposalStatus.PENDING.value,
                    Proposal.expires_at <= now,
                    Proposal.deleted_at.is_(None),
                )
                .values(status=ProposalStatus.EXPIRED.value, decided_at=now)
                .returning(Proposal.id)
            )
        )
        .scalars()
        .all()
    )
    if ids:
        await audit_service.record(
            session,
            action="proposal.expired",
            entity_type="proposal",
            meta={
                "count": len(ids),
                "proposal_ids": [str(i) for i in ids[:50]],
            },
        )
    return [str(i) for i in ids]


# ── Queries ────────────────────────────────────────────────


async def list_proposals(
    session: AsyncSession,
    actor: TokenPayload,
    *,
    status: str | None = None,
    proposal_type: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Proposal], int]:
    stmt = select(Proposal).where(
        Proposal.org_id == actor.org_id,
        Proposal.deleted_at.is_(None),
    )
    if status:
        stmt = stmt.where(Proposal.status == status)
    if proposal_type:
        stmt = stmt.where(Proposal.proposal_type == proposal_type)

    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (
        await session.execute(
            stmt.order_by(Proposal.created_at.desc()).limit(limit).offset(offset)
        )
    ).scalars().all()
    return list(rows), int(total)
