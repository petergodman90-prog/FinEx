"""Proposal API — staged writes with idempotency, approval and expiry."""

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
from app.schemas.proposal import (
    ProposalCreate,
    ProposalDecision,
    ProposalListOut,
    ProposalOut,
)
from app.services import proposal_service

router = APIRouter(prefix="/proposals", tags=["proposals"])


@router.get(
    "",
    response_model=Page[ProposalOut],
    dependencies=[Depends(require_permission("proposals:read"))],
)
async def list_proposals(
    status_filter: str | None = Query(None, alias="status", max_length=20),
    proposal_type: str | None = Query(None, max_length=50),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> Page[ProposalOut]:
    rows, total = await proposal_service.list_proposals(
        session,
        user,
        status=status_filter,
        proposal_type=proposal_type,
        limit=limit,
        offset=offset,
    )
    return Page[ProposalOut](
        items=[proposal_service.serialize_proposal(p) for p in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    response_model=ProposalListOut,
    dependencies=[Depends(require_permission("proposals:create"))],
)
async def create_proposal(
    data: ProposalCreate,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> ProposalListOut:
    """Create a staged write. Replaying an identical pending proposal returns
    the existing row with ``created=false`` (idempotent — safe for AI/WhatsApp
    retries)."""
    proposal, created = await proposal_service.create_proposal(session, data, user)
    return ProposalListOut(
        proposal=proposal_service.serialize_proposal(proposal), created=created
    )


@router.post(
    "/expire",
    dependencies=[Depends(require_permission("proposals:expire"))],
)
async def expire_proposals(
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> dict:
    """Sweep: mark pending-but-expired proposals as expired (call from a
    worker or cron in production)."""
    expired = await proposal_service.expire_pending(session, user)
    return {"expired": expired, "count": len(expired)}


@router.get(
    "/{proposal_id}",
    response_model=ProposalOut,
    dependencies=[Depends(require_permission("proposals:read"))],
)
async def get_proposal(
    proposal_id: uuid.UUID,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> ProposalOut:
    proposal = await proposal_service.get_proposal(session, user, proposal_id)
    return proposal_service.serialize_proposal(proposal)


@router.post(
    "/{proposal_id}/approve",
    response_model=ProposalOut,
    dependencies=[Depends(require_permission("proposals:approve"))],
)
async def approve_proposal(
    proposal_id: uuid.UUID,
    data: ProposalDecision,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> ProposalOut:
    """Approve a pending proposal (spend cap + expiry enforced)."""
    proposal = await proposal_service.approve_proposal(
        session, proposal_id, user, data.note
    )
    return proposal_service.serialize_proposal(proposal)


@router.post(
    "/{proposal_id}/reject",
    response_model=ProposalOut,
    dependencies=[Depends(require_permission("proposals:approve"))],
)
async def reject_proposal(
    proposal_id: uuid.UUID,
    data: ProposalDecision,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> ProposalOut:
    proposal = await proposal_service.reject_proposal(
        session, proposal_id, user, data.note
    )
    return proposal_service.serialize_proposal(proposal)
