"""Expense tracking schemas."""

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

Currency = Annotated[
    str,
    StringConstraints(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$"),
]
AccountCode = Annotated[str, StringConstraints(min_length=1, max_length=20)]
Category = Annotated[str, StringConstraints(min_length=1, max_length=50)]
Vendor = Annotated[str, StringConstraints(max_length=200)]
Description = Annotated[str, StringConstraints(max_length=2_000)]


class ExpensePaymentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: Literal["cash", "payable"] = "cash"
    cash_account_code: AccountCode = "1000"
    supplier_id: uuid.UUID | None = None
    due_on: date | None = None


class ExpenseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    currency: Currency
    effective_at: datetime
    expense_account_code: AccountCode | None = None
    category: Category | None = None
    vendor: Vendor | None = None
    description: Description | None = None
    payment: ExpensePaymentCreate = Field(default_factory=ExpensePaymentCreate)
    expires_in_hours: int = Field(default=24, ge=1, le=72)
    idempotency_key: Annotated[str, StringConstraints(max_length=128)] | None = None

    @field_validator("currency", mode="before")
    @classmethod
    def _normalise_currency(cls, value: str) -> str:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator(
        "title",
        "expense_account_code",
        "category",
        "vendor",
        "description",
        mode="before",
    )
    @classmethod
    def _trim(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def _validate_rules(self) -> "ExpenseCreate":
        if not self.expense_account_code and not self.category:
            raise ValueError(
                "An expense needs an expense account code or a category"
            )
        if self.payment.method == "payable" and self.payment.supplier_id is None:
            raise ValueError("A payable expense requires supplier_id")
        return self


class ExpenseOut(BaseModel):
    id: uuid.UUID
    proposal_type: str
    title: str
    status: str
    amount: Decimal | None = None
    currency: str | None = None
    effective_at: datetime | None = None
    expense_account_code: str | None = None
    category: str | None = None
    vendor: str | None = None
    description: str | None = None
    payment_method: str = "cash"
    cash_account_code: str | None = None
    supplier_id: uuid.UUID | None = None
    due_on: date | None = None
    journal_entry_id: uuid.UUID | None = None
    posted_at: datetime | None = None
    posting_error: dict | None = None
    created_at: datetime
    updated_at: datetime


class ExpenseCreateOut(BaseModel):
    expense: ExpenseOut
    created: bool
