"""Read-only ledger workspace API.

The financial reports API returns statement totals; this router exposes the
underlying chart of accounts, journal entries and per-account statements that a
ledger workspace needs. Everything is derived from the immutable journal and
the database read views: the browser only formats what these endpoints return.

All routes are organisation-scoped through ``get_rls_session`` and require the
same ``reports:read`` permission as the financial reports they support.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TokenPayload
from app.core.dependencies import (
    get_current_user,
    get_rls_session,
    require_permission,
)
from app.schemas.common import Page
from app.schemas.ledger import (
    AccountingPeriodOut,
    GeneralLedgerOut,
    JournalActivityLineOut,
    JournalEntryDetailOut,
    JournalEntryDocumentsOut,
    JournalEntryOut,
    JournalLineOut,
    LedgerAccountOut,
    LedgerAccountRefOut,
)
from app.services import ledger_service
from app.services.errors import NotFoundError

router = APIRouter(prefix="/ledger", tags=["ledger"])

AccountType = Literal[
    "asset", "liability", "equity", "revenue", "expense", "cogs"
]
EntryStatus = Literal["posted", "reversed"]


def _account_ref(account: ledger_service.Account) -> LedgerAccountRefOut:
    return LedgerAccountRefOut(
        id=account.id,
        code=account.code,
        name=account.name,
        account_type=account.account_type,
        is_control=account.is_control,
        is_cash_account=account.is_cash_account,
        is_active=account.is_active,
    )


def _account_out(
    item: ledger_service.AccountBalance,
) -> LedgerAccountOut:
    return LedgerAccountOut(
        **_account_ref(item.account).model_dump(),
        debit_total=item.debit_total,
        credit_total=item.credit_total,
        balance=item.normal_balance,
    )


def _entry_out(entry: ledger_service.JournalEntry) -> JournalEntryOut:
    return JournalEntryOut(
        id=entry.id,
        entry_number=entry.entry_number,
        effective_at=entry.effective_at,
        memo=entry.memo,
        status=entry.status,
        source_type=entry.source_type,
        source_id=entry.source_id,
        proposal_type=entry.proposal_type,
        reversal_of_id=entry.reversal_of_id,
        total_debit=entry.total_debit,
        total_credit=entry.total_credit,
    )


def _journal_line_out(line: ledger_service.JournalLine) -> JournalLineOut:
    return JournalLineOut(
        line_number=line.line_number,
        account_id=line.account_id,
        account_code=line.account_code,
        account_name=line.account_name,
        account_type=line.account_type,
        debit=line.debit,
        credit=line.credit,
        description=line.description,
    )


def _activity_line_out(
    line: ledger_service.JournalActivityLine,
) -> JournalActivityLineOut:
    return JournalActivityLineOut(
        journal_entry_id=line.journal_entry_id,
        entry_number=line.entry_number,
        effective_at=line.effective_at,
        memo=line.memo,
        status=line.status,
        line_number=line.line_number,
        description=line.description,
        debit=line.debit,
        credit=line.credit,
        running_balance=line.running_balance,
    )


@router.get(
    "/accounts",
    response_model=list[LedgerAccountOut],
    dependencies=[Depends(require_permission("reports:read"))],
)
async def list_accounts(
    include_inactive: bool = False,
    account_type: AccountType | None = Query(
        None, description="Filter by account type"
    ),
    q: str | None = Query(
        None, max_length=200, description="Match account code or name"
    ),
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> list[LedgerAccountOut]:
    """Chart of accounts with all-time ledger totals and normal balance."""
    items = await ledger_service.list_account_balances(
        session,
        user.org_id,
        include_inactive=include_inactive,
        account_type=account_type,
        q=q,
    )
    return [_account_out(item) for item in items]


@router.get(
    "/accounts/{account_id}/activity",
    response_model=GeneralLedgerOut,
    dependencies=[Depends(require_permission("reports:read"))],
)
async def get_account_activity(
    account_id: uuid.UUID,
    start: date | None = Query(
        None, description="Inclusive period start (unbounded when omitted)"
    ),
    end: date | None = Query(
        None, description="Inclusive period end (unbounded when omitted)"
    ),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> GeneralLedgerOut:
    """General-ledger statement for one account.

    Opening and closing balances are net debit balances (debit - credit), so a
    credit-normal account such as revenue closes with a negative value.
    """
    activity = await ledger_service.account_activity(
        session,
        user.org_id,
        account_id,
        start=start,
        end=end,
        limit=limit,
        offset=offset,
    )
    if activity is None:
        raise NotFoundError("Ledger account not found")
    return GeneralLedgerOut(
        account=_account_ref(activity.account),
        period_start=activity.period_start,
        period_end=activity.period_end,
        opening_balance=activity.opening_balance,
        debit_total=activity.debit_total,
        credit_total=activity.credit_total,
        closing_balance=activity.closing_balance,
        total=activity.total,
        lines=[_activity_line_out(line) for line in activity.lines],
    )


@router.get(
    "/journal-entries",
    response_model=Page[JournalEntryOut],
    dependencies=[Depends(require_permission("reports:read"))],
)
async def list_journal_entries(
    start: date | None = Query(
        None, description="Inclusive effective-date start"
    ),
    end: date | None = Query(
        None, description="Inclusive effective-date end"
    ),
    account_id: uuid.UUID | None = Query(
        None, description="Only entries with a line on this account"
    ),
    status_filter: EntryStatus | None = Query(
        None, alias="status", description="Entry status"
    ),
    source_type: str | None = Query(
        None,
        max_length=60,
        description="Raw posting source (proposal-backed entries use 'proposal')",
    ),
    proposal_type: str | None = Query(
        None,
        max_length=50,
        description="Domain type, e.g. sale, expense or opening_stock",
    ),
    q: str | None = Query(
        None, max_length=200, description="Match the entry memo"
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> Page[JournalEntryOut]:
    """Posted and reversed journal entries, newest effective date first."""
    items, total = await ledger_service.list_journal_entries(
        session,
        user.org_id,
        limit=limit,
        offset=offset,
        start=start,
        end=end,
        account_id=account_id,
        status=status_filter,
        source_type=source_type,
        proposal_type=proposal_type,
        q=q,
    )
    return Page[JournalEntryOut](
        items=[_entry_out(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/journal-entries/{entry_id}",
    response_model=JournalEntryDetailOut,
    dependencies=[Depends(require_permission("reports:read"))],
)
async def get_journal_entry(
    entry_id: uuid.UUID,
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> JournalEntryDetailOut:
    """One journal entry with its balanced lines and linked documents."""
    entry = await ledger_service.get_journal_entry(
        session, user.org_id, entry_id
    )
    if entry is None:
        raise NotFoundError("Journal entry not found")
    lines = await ledger_service.get_journal_lines(
        session, user.org_id, entry_id
    )
    documents = (
        await ledger_service.get_posting_documents(
            session, user.org_id, [entry_id]
        )
    ).get(entry_id)
    return JournalEntryDetailOut(
        **_entry_out(entry).model_dump(),
        lines=[_journal_line_out(line) for line in lines],
        documents=(
            JournalEntryDocumentsOut(
                receipt_id=documents.receipt_id,
                receipt_number=documents.receipt_number,
                receipt_type=documents.receipt_type,
                open_item_id=documents.open_item_id,
            )
            if documents is not None
            else None
        ),
    )


@router.get(
    "/periods",
    response_model=list[AccountingPeriodOut],
    dependencies=[Depends(require_permission("reports:read"))],
)
async def list_accounting_periods(
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> list[AccountingPeriodOut]:
    """Accounting periods, including whether each period is closed."""
    periods = await ledger_service.list_accounting_periods(
        session, user.org_id
    )
    return [AccountingPeriodOut(**period) for period in periods]
