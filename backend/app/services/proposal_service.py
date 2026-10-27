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
from app.services import audit_service, posting_service
from app.services.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.services.posting_service import PostingError


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
        journal_entry_id=proposal.journal_entry_id,
        posting_error=dict(proposal.posting_error)
        if proposal.posting_error is not None
        else None,
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
        "journal_entry_id": str(proposal.journal_entry_id)
        if proposal.journal_entry_id is not None
        else None,
        "posting_error": proposal.posting_error,
    }


def _is_manual(proposal: Proposal) -> bool:
    return proposal.proposal_type.strip().lower() == "manual"


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


async def _attempt_posting(
    session: AsyncSession, proposal: Proposal, actor: TokenPayload
) -> Proposal:
    """Post an approved proposal and record the ledger id or failure state.

    The posting service uses a savepoint, so a failed attempt rolls back every
    financial write while leaving this proposal update committable.
    """
    try:
        result = await posting_service.post_proposal(
            session,
            org_id=proposal.org_id,
            proposal_id=proposal.id,
            proposal_type=proposal.proposal_type,
            payload=proposal.payload,
            amount=proposal.amount,
            currency=proposal.currency,
            requested_by=uuid.UUID(actor.sub),
            role=actor.role,
        )
    except PostingError as exc:
        proposal.posting_error = exc.as_dict()
        await session.flush()
        await session.refresh(proposal)
        await audit_service.record(
            session,
            action="proposal.posting_failed",
            entity_type="proposal",
            entity_id=proposal.id,
            after_state=_audit_snapshot(proposal),
            meta={"error": exc.as_dict()},
        )
        return proposal

    proposal.status = ProposalStatus.APPLIED.value
    proposal.journal_entry_id = result.journal_entry_id
    proposal.posting_error = None
    await session.flush()
    await session.refresh(proposal)
    await audit_service.record(
        session,
        action="proposal.applied",
        entity_type="proposal",
        entity_id=proposal.id,
        after_state=_audit_snapshot(proposal),
        meta={
            "journal_entry_id": str(result.journal_entry_id),
            "replayed": result.replayed,
        },
    )
    return proposal


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
    if approve and not _is_manual(proposal):
        return await _attempt_posting(session, proposal, actor)
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


async def post_approved_proposal(
    session: AsyncSession,
    proposal_id: uuid.UUID,
    actor: TokenPayload,
) -> Proposal:
    """Retry posting for an approved proposal; applied proposals replay."""
    proposal = await get_proposal(session, actor, proposal_id)
    if _is_manual(proposal):
        raise ConflictError(
            "Manual proposals are informational and are never posted"
        )
    if (
        proposal.status == ProposalStatus.APPLIED.value
        and proposal.journal_entry_id is not None
    ):
        return proposal
    if proposal.status not in {
        ProposalStatus.APPROVED.value,
        ProposalStatus.APPLIED.value,
    }:
        raise ConflictError(
            f"Proposal is '{proposal.status}' and can no longer be posted"
        )
    return await _attempt_posting(session, proposal, actor)


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
