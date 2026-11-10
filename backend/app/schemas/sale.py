"""Sales and credit-note schemas."""

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
Description = Annotated[str, StringConstraints(max_length=500)]
Memo = Annotated[str, StringConstraints(max_length=2_000)]
Notes = Annotated[str, StringConstraints(max_length=2_000)]
Quantity = Annotated[Decimal, Field(gt=0, max_digits=19, decimal_places=4)]
Money = Annotated[Decimal, Field(ge=0, max_digits=19, decimal_places=4)]


class SaleLineCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: uuid.UUID
    quantity: Quantity
    unit_price: Money | None = None
    unit_cost: Money | None = None
    description: Description | None = None

    @field_validator("description", mode="before")
    @classmethod
    def _trim(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class SalePaymentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: Literal["cash", "receivable"] = "cash"
    cash_account_code: AccountCode = "1000"
    due_on: date | None = None
    issue_receipt: bool | None = None
    receipt_notes: Notes | None = None

    @field_validator("cash_account_code", "receipt_notes", mode="before")
    @classmethod
    def _trim(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def _validate_rules(self) -> "SalePaymentCreate":
        if self.method == "receivable" and self.issue_receipt:
            raise ValueError("A receivable sale cannot issue a receipt")
        return self


class SaleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: uuid.UUID
    currency: Currency
    effective_at: datetime
    lines: list[SaleLineCreate] = Field(min_length=1)
    payment: SalePaymentCreate = Field(default_factory=SalePaymentCreate)
    memo: Memo | None = None
    expires_in_hours: int = Field(default=24, ge=1, le=72)
    idempotency_key: Annotated[str, StringConstraints(max_length=128)] | None = None

    @field_validator("currency", mode="before")
    @classmethod
    def _normalise_currency(cls, value: str) -> str:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("memo", mode="before")
    @classmethod
    def _trim(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def _unique_products(self) -> "SaleCreate":
        product_ids = [line.product_id for line in self.lines]
        if len(product_ids) != len(set(product_ids)):
            raise ValueError("A payload may contain each product only once")
        return self


class CreditNoteLineCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: uuid.UUID | None = None
    quantity: Quantity
    unit_price: Money
    unit_cost: Money | None = None
    description: Description | None = None

    @field_validator("description", mode="before")
    @classmethod
    def _trim(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class CreditNoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: uuid.UUID | None = None
    currency: Currency | None = None
    effective_at: datetime
    lines: list[CreditNoteLineCreate] = Field(min_length=1)
    original_receipt_id: uuid.UUID | None = None
    restock: bool = True
    refund_method: Literal["cash"] = "cash"
    cash_account_code: AccountCode = "1000"
    notes: Notes | None = None
    memo: Memo | None = None
    expires_in_hours: int = Field(default=24, ge=1, le=72)
    idempotency_key: Annotated[str, StringConstraints(max_length=128)] | None = None

    @field_validator("currency", mode="before")
    @classmethod
    def _normalise_currency(cls, value: str | None) -> str | None:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("cash_account_code", "notes", "memo", mode="before")
    @classmethod
    def _trim(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def _validate_rules(self) -> "CreditNoteCreate":
        if self.original_receipt_id is None and (
            self.customer_id is None or self.currency is None
        ):
            raise ValueError(
                "A credit note without an original receipt requires customer_id and currency"
            )
        if self.restock and any(
            line.product_id is None for line in self.lines
        ):
            raise ValueError("Restocked credit-note lines require a product_id")
        product_ids = [
            line.product_id for line in self.lines if line.product_id is not None
        ]
        if len(product_ids) != len(set(product_ids)):
            raise ValueError("A payload may contain each product only once")
        return self


class SaleLineOut(BaseModel):
    product_id: uuid.UUID
    sku: str | None = None
    name: str | None = None
    quantity: Decimal
    unit_price: Decimal
    unit_cost: Decimal | None = None
    line_total: Decimal
    description: str | None = None


class SalePaymentOut(BaseModel):
    method: str
    cash_account_code: str
    due_on: date | None = None
    issue_receipt: bool = False
    receipt_notes: str | None = None


class SaleOut(BaseModel):
    id: uuid.UUID
    proposal_type: str
    title: str
    status: str
    amount: Decimal | None = None
    currency: str | None = None
    effective_at: datetime | None = None
    customer_id: uuid.UUID | None = None
    customer_name: str | None = None
    lines: list[SaleLineOut]
    payment: SalePaymentOut
    memo: str | None = None
    journal_entry_id: uuid.UUID | None = None
    receipt_id: uuid.UUID | None = None
    receipt_number: int | None = None
    open_item_id: uuid.UUID | None = None
    posted_at: datetime | None = None
    posting_error: dict | None = None
    created_at: datetime
    updated_at: datetime


class SaleCreateOut(BaseModel):
    sale: SaleOut
    created: bool


class CreditNoteLineOut(BaseModel):
    product_id: uuid.UUID | None = None
    sku: str | None = None
    name: str | None = None
    quantity: Decimal
    unit_price: Decimal
    unit_cost: Decimal | None = None
    line_total: Decimal
    description: str | None = None


class CreditNoteOut(BaseModel):
    id: uuid.UUID
    proposal_type: str
    title: str
    status: str
    amount: Decimal | None = None
    currency: str | None = None
    effective_at: datetime | None = None
    customer_id: uuid.UUID | None = None
    customer_name: str | None = None
    original_receipt_id: uuid.UUID | None = None
    restock: bool
    refund_method: str
    cash_account_code: str
    notes: str | None = None
    memo: str | None = None
    lines: list[CreditNoteLineOut]
    journal_entry_id: uuid.UUID | None = None
    credit_note_id: uuid.UUID | None = None
    credit_note_number: int | None = None
    posted_at: datetime | None = None
    posting_error: dict | None = None
    created_at: datetime
    updated_at: datetime


class CreditNoteCreateOut(BaseModel):
    credit_note: CreditNoteOut
    created: bool
