"""
Dashboard aggregation service.

CRM counters come from the operational tables. Financial totals are read from
the immutable ledger through the report service so the browser never computes
balances or revenue.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TokenPayload
from app.models.customer import Customer
from app.models.organization import Organization
from app.models.proposal import Proposal, ProposalStatus
from app.models.touchpoint import Touchpoint
from app.services import ledger_service

_RECENT_LIMIT = 10
_ALL_TIME_START = date(1900, 1, 1)


async def _financial_metrics(
    session: AsyncSession, org_id: uuid.UUID
) -> dict:
    today = datetime.now(timezone.utc).date()
    profit_and_loss = await ledger_service.profit_and_loss(
        session,
        org_id,
        start=_ALL_TIME_START,
        end=today,
    )
    debts = await ledger_service.debt_summary(session, org_id)
    orders_total = (
        await session.scalar(
            select(func.count())
            .select_from(Proposal)
            .where(
                Proposal.org_id == org_id,
                Proposal.proposal_type == "sale",
                Proposal.status == ProposalStatus.APPLIED.value,
                Proposal.deleted_at.is_(None),
            )
        )
        or 0
    )
    currency = await session.scalar(
        select(Organization.currency).where(Organization.id == org_id)
    )

    return {
        "currency": currency or "USD",
        "revenue_total": profit_and_loss.revenue_total,
        "cogs_total": profit_and_loss.cogs_total,
        "operating_expense_total": profit_and_loss.operating_expense_total,
        "expenses_total": (
            profit_and_loss.cogs_total
            + profit_and_loss.operating_expense_total
        ),
        "net_profit": profit_and_loss.net_profit,
        "orders_total": int(orders_total),
        "receivables_outstanding": debts.total_receivables,
        "payables_outstanding": debts.total_payables,
        "overdue_receivables": debts.overdue_receivables,
        "overdue_payables": debts.overdue_payables,
        "total_debtors": debts.total_debtors,
        "total_creditors": debts.total_creditors,
    }


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

    financials = await _financial_metrics(session, org_id)

    return {
        "org_id": org_id,
        "generated_at": now,
        "customers_total": int(customers_total),
        "customers_new_30d": int(customers_new_30d),
        "touchpoints_30d": int(touchpoints_30d),
        "proposals_pending": int(proposals_pending),
        "proposals_pending_amount": proposals_pending_amount,
        **financials,
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
