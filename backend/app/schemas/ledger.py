"""Read models for the ledger workspace API."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel


class LedgerAccountRefOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    account_type: str
    is_control: bool
    is_cash_account: bool
    is_active: bool


class LedgerAccountOut(LedgerAccountRefOut):
    debit_total: Decimal
    credit_total: Decimal
    balance: Decimal


class JournalLineOut(BaseModel):
    line_number: int
    account_id: uuid.UUID
    account_code: str
    account_name: str
    account_type: str
    debit: Decimal
    credit: Decimal
    description: str | None


class JournalEntryOut(BaseModel):
    id: uuid.UUID
    entry_number: int
    effective_at: datetime
    memo: str | None
    status: str
    source_type: str | None
    source_id: uuid.UUID | None
    proposal_type: str | None
    reversal_of_id: uuid.UUID | None
    total_debit: Decimal
    total_credit: Decimal


class JournalEntryDocumentsOut(BaseModel):
    receipt_id: uuid.UUID | None
    receipt_number: int | None
    receipt_type: str | None
    open_item_id: uuid.UUID | None


class JournalEntryDetailOut(JournalEntryOut):
    lines: list[JournalLineOut]
    documents: JournalEntryDocumentsOut | None = None


class JournalActivityLineOut(BaseModel):
    journal_entry_id: uuid.UUID
    entry_number: int
    effective_at: datetime
    memo: str | None
    status: str
    line_number: int
    description: str | None
    debit: Decimal
    credit: Decimal
    running_balance: Decimal


class GeneralLedgerOut(BaseModel):
    account: LedgerAccountRefOut
    period_start: date | None
    period_end: date | None
    opening_balance: Decimal
    debit_total: Decimal
    credit_total: Decimal
    closing_balance: Decimal
    total: int
    lines: list[JournalActivityLineOut]


class AccountingPeriodOut(BaseModel):
    id: uuid.UUID
    period_start: date
    period_end: date
    is_closed: bool
    closed_at: datetime | None
