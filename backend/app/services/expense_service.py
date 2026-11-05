"""Expense tracking on top of staged proposals."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TokenPayload
from app.models.proposal import Proposal
from app.schemas.expense import ExpenseCreate, ExpenseOut
from app.schemas.proposal import ProposalCreate
from app.services import ledger_service, proposal_service
from app.services.errors import DomainValidationError, NotFoundError
from app.services.posting_service import EXPENSE_CATEGORY_ACCOUNTS


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _uuid_or_none(value: object) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def _date_or_none(value: object) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def _datetime_or_none(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def serialize_expense(proposal: Proposal) -> ExpenseOut:
    payload = dict(proposal.payload or {})
    payment = dict(payload.get("payment") or {})
    category = payload.get("category")
    account_code = payload.get("expense_account_code")
    if not account_code and category:
        account_code = EXPENSE_CATEGORY_ACCOUNTS.get(str(category).strip().lower())
    return ExpenseOut(
        id=proposal.id,
        proposal_type=proposal.proposal_type,
        title=proposal.title,
        status=proposal.status,
        amount=proposal.amount,
        currency=proposal.currency,
        effective_at=_datetime_or_none(payload.get("effective_at")),
        expense_account_code=account_code,
        category=category,
        vendor=payload.get("vendor"),
        description=payload.get("description"),
        payment_method=str(payment.get("method", "cash")),
        cash_account_code=payment.get("cash_account_code"),
        supplier_id=_uuid_or_none(payment.get("supplier_id")),
        due_on=_date_or_none(payment.get("due_on")),
        journal_entry_id=proposal.journal_entry_id,
        posted_at=proposal.posted_at,
        posting_error=dict(proposal.posting_error)
        if proposal.posting_error is not None
        else None,
        created_at=proposal.created_at,
        updated_at=proposal.updated_at,
    )


async def create_expense(
    session: AsyncSession, data: ExpenseCreate, actor: TokenPayload
) -> tuple[ExpenseOut, bool]:
    category = data.category.strip().lower() if data.category else None
    if not data.expense_account_code and category:
        if category not in EXPENSE_CATEGORY_ACCOUNTS:
            raise DomainValidationError(
                "Unknown expense category",
                details={"category": category},
            )

    if data.payment.supplier_id is not None:
        supplier = await ledger_service.get_supplier(
            session, actor.org_id, data.payment.supplier_id
        )
        if supplier is None or not supplier.is_active:
            raise DomainValidationError(
                "The supplier does not exist in this organisation",
                details={"supplier_id": str(data.payment.supplier_id)},
            )

    payment: dict = {
        "method": data.payment.method,
        "cash_account_code": data.payment.cash_account_code,
    }
    if data.payment.supplier_id is not None:
        payment["supplier_id"] = str(data.payment.supplier_id)
    if data.payment.due_on is not None:
        payment["due_on"] = data.payment.due_on.isoformat()

    payload: dict = {
        "amount": f"{data.amount:.2f}",
        "currency": data.currency,
        "effective_at": _utc_iso(data.effective_at),
        "payment": payment,
    }
    if data.expense_account_code:
        payload["expense_account_code"] = data.expense_account_code
    if category:
        payload["category"] = category
    if data.vendor:
        payload["vendor"] = data.vendor.strip()
    if data.description:
        payload["description"] = data.description.strip()

    proposal, created = await proposal_service.create_proposal(
        session,
        ProposalCreate(
            proposal_type="expense",
            title=data.title.strip(),
            payload=payload,
            amount=data.amount,
            currency=data.currency,
            expires_in_hours=data.expires_in_hours,
            idempotency_key=data.idempotency_key,
        ),
        actor,
    )
    return serialize_expense(proposal), created


async def list_expenses(
    session: AsyncSession,
    actor: TokenPayload,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[ExpenseOut], int]:
    rows, total = await proposal_service.list_proposals(
        session,
        actor,
        status=status,
        proposal_type="expense",
        limit=limit,
        offset=offset,
    )
    return [serialize_expense(row) for row in rows], total


async def get_expense(
    session: AsyncSession, actor: TokenPayload, expense_id: uuid.UUID
) -> ExpenseOut:
    proposal = await proposal_service.get_proposal(session, actor, expense_id)
    if proposal.proposal_type.strip().lower() != "expense":
        raise NotFoundError("Expense not found")
    return serialize_expense(proposal)
