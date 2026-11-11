"""Inventory read models and stock proposal schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
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

Quantity = Annotated[Decimal, Field(gt=0, max_digits=19, decimal_places=4)]
UnitCost = Annotated[Decimal, Field(ge=0, max_digits=19, decimal_places=4)]
Description = Annotated[str, StringConstraints(max_length=500)]
Memo = Annotated[str, StringConstraints(max_length=2_000)]
Reason = Annotated[str, StringConstraints(min_length=1, max_length=500)]

StockMovementType = Literal[
    "opening_balance",
    "purchase",
    "sale",
    "sale_reversal",
    "adjustment_in",
    "adjustment_out",
    "stock_take",
    "return_in",
    "return_out",
]


class StockLevelOut(BaseModel):
    product_id: uuid.UUID
    sku: str
    name: str
    quantity_on_hand: Decimal
    reorder_point: Decimal
    needs_reorder: bool


class StockValuationRowOut(BaseModel):
    product_id: uuid.UUID
    sku: str
    name: str
    quantity_on_hand: Decimal
    book_value: Decimal
    average_unit_cost: Decimal


class StockValuationOut(BaseModel):
    rows: list[StockValuationRowOut]
    total_book_value: Decimal
    inventory_account_balance: Decimal
    is_reconciled: bool


class StockMovementOut(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    sku: str
    name: str
    movement_type: StockMovementType
    quantity_delta: Decimal
    unit_cost: Decimal
    source_type: str
    source_id: uuid.UUID
    journal_entry_id: uuid.UUID
    occurred_at: datetime
    note: str | None = None
    created_at: datetime


class OpeningStockLineCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: uuid.UUID
    quantity: Quantity
    unit_cost: UnitCost | None = None
    description: Description | None = None

    @field_validator("description", mode="before")
    @classmethod
    def _trim(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class OpeningStockCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    effective_at: datetime
    lines: list[OpeningStockLineCreate] = Field(min_length=1)
    memo: Memo | None = None
    expires_in_hours: int = Field(default=24, ge=1, le=72)
    idempotency_key: Annotated[str, StringConstraints(max_length=128)] | None = None

    @field_validator("memo", mode="before")
    @classmethod
    def _trim_memo(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def _unique_products(self) -> "OpeningStockCreate":
        product_ids = [line.product_id for line in self.lines]
        if len(product_ids) != len(set(product_ids)):
            raise ValueError("A payload may contain each product only once")
        return self


class StockAdjustmentLineCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: uuid.UUID
    quantity: Quantity
    unit_cost: UnitCost | None = None
    description: Description | None = None

    @field_validator("description", mode="before")
    @classmethod
    def _trim(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class StockAdjustmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: Literal["in", "out"]
    effective_at: datetime
    lines: list[StockAdjustmentLineCreate] = Field(min_length=1)
    reason: Reason
    memo: Memo | None = None
    expires_in_hours: int = Field(default=24, ge=1, le=72)
    idempotency_key: Annotated[str, StringConstraints(max_length=128)] | None = None

    @field_validator("reason", "memo", mode="before")
    @classmethod
    def _trim(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def _unique_products(self) -> "StockAdjustmentCreate":
        product_ids = [line.product_id for line in self.lines]
        if len(product_ids) != len(set(product_ids)):
            raise ValueError("A payload may contain each product only once")
        return self


class StockProposalOut(BaseModel):
    proposal: ProposalOut
    created: bool
