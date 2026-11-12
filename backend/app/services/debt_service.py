"""Debt tracking and settlement staging."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TokenPayload
from app.models.organization import Organization
from app.schemas.debt import (
    AgingBucketOut,
    DebtAgingOut,
    DebtOut,
    DebtSummaryOut,
    SettlementCreate,
    SettlementOut,
    SettlementProposalOut,
)
from app.schemas.proposal import ProposalCreate
from app.services import ledger_service, proposal_service
from app.services.errors import ConflictError, DomainValidationError, NotFoundError

ZERO = Decimal("0")


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def serialize_debt(item: ledger_service.OpenItem) -> DebtOut:
    is_overdue = (
        item.due_on is not None
        and item.outstanding_amount > ZERO
        and item.due_on < datetime.now(timezone.utc).date()
    )
    return DebtOut(
        id=item.id,
        item_type=item.item_type,
        counterparty_id=item.counterparty_id,
        counterparty_name=item.counterparty_name,
        original_amount=item.original_amount,
        allocated_amount=item.allocated_amount,
        outstanding_amount=item.outstanding_amount,
        due_on=item.due_on,
        opened_at=item.opened_at,
        aging_bucket=item.aging_bucket,
        is_overdue=is_overdue,
    )


def serialize_settlement(item: ledger_service.Settlement) -> SettlementOut:
    return SettlementOut(
        id=item.id,
        open_item_id=item.open_item_id,
        journal_entry_id=item.journal_entry_id,
        amount=item.amount,
        allocated_at=item.allocated_at,
        reversal_of_id=item.reversal_of_id,
        is_reversed=item.is_reversed,
    )


async def list_debts(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    item_type: str | None = None,
    outstanding_only: bool = True,
    overdue_only: bool = False,
    counterparty_id: uuid.UUID | None = None,
    aging_bucket: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[DebtOut], int]:
    rows, total = await ledger_service.list_open_items_page(
        session,
        org_id,
        item_type=item_type,
        outstanding_only=outstanding_only,
        overdue_only=overdue_only,
        counterparty_id=counterparty_id,
        aging_bucket=aging_bucket,
        q=q,
        limit=limit,
        offset=offset,
    )
    return [serialize_debt(row) for row in rows], total


async def get_debt(
    session: AsyncSession, org_id: uuid.UUID, debt_id: uuid.UUID
) -> DebtOut:
    item = await ledger_service.get_open_item(session, org_id, debt_id)
    if item is None:
        raise NotFoundError("Debt not found")
    return serialize_debt(item)


async def get_summary(
    session: AsyncSession, org_id: uuid.UUID
) -> DebtSummaryOut:
    summary = await ledger_service.debt_summary(session, org_id)
    currency = await session.scalar(
        select(Organization.currency).where(Organization.id == org_id)
    )
    return DebtSummaryOut(
        currency=currency or "USD",
        total_receivables=summary.total_receivables,
        total_payables=summary.total_payables,
        total_outstanding=summary.total_outstanding,
        total_debtors=summary.total_debtors,
        total_creditors=summary.total_creditors,
        overdue_receivables=summary.overdue_receivables,
        overdue_payables=summary.overdue_payables,
        overdue_count=summary.overdue_count,
        generated_at=datetime.now(timezone.utc),
    )


async def get_aging(
    session: AsyncSession, org_id: uuid.UUID, item_type: str
) -> DebtAgingOut:
    try:
        report = await ledger_service.debt_aging(
            session, org_id, item_type=item_type
        )
    except ValueError as exc:
        raise DomainValidationError(str(exc)) from exc
    return DebtAgingOut(
        item_type=report.item_type,
        total_outstanding=report.total_outstanding,
        buckets=[
            AgingBucketOut(
                bucket=bucket.bucket,
                item_count=bucket.item_count,
                outstanding_amount=bucket.outstanding_amount,
            )
            for bucket in report.buckets
        ],
    )


async def list_settlements(
    session: AsyncSession,
    org_id: uuid.UUID,
    debt_id: uuid.UUID,
    *,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[SettlementOut], int]:
    item = await ledger_service.get_open_item(session, org_id, debt_id)
    if item is None:
        raise NotFoundError("Debt not found")
    rows, total = await ledger_service.list_settlements(
        session, org_id, debt_id, limit=limit, offset=offset
    )
    return [serialize_settlement(row) for row in rows], total


async def create_settlement(
    session: AsyncSession,
    org_id: uuid.UUID,
    debt_id: uuid.UUID,
    data: SettlementCreate,
    actor: TokenPayload,
) -> SettlementProposalOut:
    item = await ledger_service.get_open_item(session, org_id, debt_id)
    if item is None:
        raise NotFoundError("Debt not found")
    if item.outstanding_amount <= ZERO:
        raise ConflictError("This debt is already settled")
    if data.amount > item.outstanding_amount:
        raise DomainValidationError(
            "Settlement amount exceeds the outstanding balance",
            details={
                "outstanding_amount": str(item.outstanding_amount),
                "settlement_amount": str(data.amount),
            },
        )
    if data.issue_receipt and item.item_type != "customer_receivable":
        raise DomainValidationError(
            "Only a customer receivable can issue a receipt"
        )

    title = f"Settle {item.item_type.replace('_', ' ')}"
    if item.counterparty_name:
        title = f"{title} - {item.counterparty_name}"
    title = title[:200]

    payload = {
        "open_item_id": str(item.id),
        "amount": f"{data.amount:.2f}",
        "currency": data.currency,
        "effective_at": _utc_iso(data.effective_at),
        "cash_account_code": data.cash_account_code,
        "issue_receipt": data.issue_receipt,
        "notes": data.notes,
    }
    proposal, created = await proposal_service.create_proposal(
        session,
        ProposalCreate(
            proposal_type="settlement",
            title=title,
            payload=payload,
            amount=data.amount,
            currency=data.currency,
            expires_in_hours=data.expires_in_hours,
            idempotency_key=data.idempotency_key,
        ),
        actor,
    )
    return SettlementProposalOut(
        proposal=proposal_service.serialize_proposal(proposal),
        created=created,
    )
