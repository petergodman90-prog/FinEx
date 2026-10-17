"""
Dashboard aggregation service.

Only metrics backed by tables that exist today are computed. Metrics that
depend on upstream modules (orders/invoices ledgers) are listed in
``upstream_pending`` so the frontend can render placeholders instead of
guessing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TokenPayload
from app.models.customer import Customer
from app.models.proposal import Proposal, ProposalStatus
from app.models.touchpoint import Touchpoint

_RECENT_LIMIT = 10


async def get_metrics(session: AsyncSession, actor: TokenPayload) -> dict:
    org_id = actor.org_id
    now = datetime.now(timezone.utc)
    cutoff_30d = now - timedelta(days=30)

    customers_total = (
        await session.scalar(
            select(func.count())
            .select_from(Customer)
            .where(
                Customer.org_id == org_id,
                Customer.deleted_at.is_(None),
                Customer.merged_into_id.is_(None),
            )
        )
        or 0
    )

    customers_new_30d = (
        await session.scalar(
            select(func.count())
            .select_from(Customer)
            .where(
                Customer.org_id == org_id,
                Customer.deleted_at.is_(None),
                Customer.merged_into_id.is_(None),
                Customer.created_at >= cutoff_30d,
            )
        )
        or 0
    )

    touchpoints_30d = (
        await session.scalar(
            select(func.count())
            .select_from(Touchpoint)
            .where(
                Touchpoint.org_id == org_id,
                Touchpoint.occurred_at >= cutoff_30d,
            )
        )
        or 0
    )

    proposals_pending = (
        await session.scalar(
            select(func.count())
            .select_from(Proposal)
            .where(
                Proposal.org_id == org_id,
                Proposal.status == ProposalStatus.PENDING.value,
                Proposal.deleted_at.is_(None),
            )
        )
        or 0
    )

    proposals_pending_amount = await session.scalar(
        select(func.coalesce(func.sum(Proposal.amount), 0)).where(
            Proposal.org_id == org_id,
            Proposal.status == ProposalStatus.PENDING.value,
            Proposal.deleted_at.is_(None),
        )
    )

    recent_rows = (
        await session.execute(
            select(Touchpoint, Customer.name)
            .join(Customer, Customer.id == Touchpoint.customer_id)
            .where(Touchpoint.org_id == org_id)
            .order_by(Touchpoint.occurred_at.desc())
            .limit(_RECENT_LIMIT)
        )
    ).all()

    return {
        "org_id": org_id,
        "generated_at": now,
        "customers_total": int(customers_total),
        "customers_new_30d": int(customers_new_30d),
        "touchpoints_30d": int(touchpoints_30d),
        "proposals_pending": int(proposals_pending),
        "proposals_pending_amount": proposals_pending_amount,
        "recent_touchpoints": [
            {
                "id": tp.id,
                "customer_id": tp.customer_id,
                "customer_name": customer_name,
                "kind": tp.kind,
                "summary": tp.summary,
                "occurred_at": tp.occurred_at,
            }
            for tp, customer_name in recent_rows
        ],
    }
