"""Debt, open-item, and settlement schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.schemas.proposal import ProposalOut

DebtItemType = Literal["customer_receivable", "supplier_payable"]
Currency = Annotated[
    str,
    StringConstraints(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$"),
]
AccountCode = Annotated[str, StringConstraints(min_length=1, max_length=20)]


class DebtOut(BaseModel):
    id: uuid.UUID
    item_type: DebtItemType
    counterparty_id: uuid.UUID
    counterparty_name: str | None = None
    original_amount: Decimal
    allocated_amount: Decimal
    outstanding_amount: Decimal
    due_on: date | None = None
    opened_at: datetime
    aging_bucket: str
    is_overdue: bool = False


class DebtSummaryOut(BaseModel):
    total_receivables: Decimal
    total_payables: Decimal
    total_outstanding: Decimal
    total_debtors: int
    total_creditors: int
    overdue_receivables: Decimal
    overdue_payables: Decimal
    overdue_count: int
    generated_at: datetime


class AgingBucketOut(BaseModel):
    bucket: str
    item_count: int
    outstanding_amount: Decimal


class DebtAgingOut(BaseModel):
    item_type: DebtItemType
    total_outstanding: Decimal
    buckets: list[AgingBucketOut]


class SettlementCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    effective_at: datetime
    currency: Currency | None = None
    cash_account_code: AccountCode = "1000"
    issue_receipt: bool = False
    notes: Annotated[str, StringConstraints(max_length=2_000)] | None = None
    expires_in_hours: int = Field(default=24, ge=1, le=72)
    idempotency_key: Annotated[str, StringConstraints(max_length=128)] | None = None

    @field_validator("currency", mode="before")
    @classmethod
    def _normalise_currency(cls, value: str | None) -> str | None:
        return value.strip().upper() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _validate_rules(self) -> "SettlementCreate":
        if self.issue_receipt and self.currency is None:
            raise ValueError("A receipt requires a currency")
        return self


class SettlementProposalOut(BaseModel):
    proposal: ProposalOut
    created: bool


class SettlementOut(BaseModel):
    id: uuid.UUID
    open_item_id: uuid.UUID
    journal_entry_id: uuid.UUID
    amount: Decimal
    allocated_at: datetime
    reversal_of_id: uuid.UUID | None = None
    is_reversed: bool = False
