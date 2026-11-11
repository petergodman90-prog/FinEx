"""Product master schemas."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Sku = Annotated[str, StringConstraints(min_length=1, max_length=50)]
ProductName = Annotated[str, StringConstraints(min_length=1, max_length=200)]
ProductUnit = Annotated[str, StringConstraints(min_length=1, max_length=20)]
ProductAmount = Annotated[
    Decimal, Field(ge=0, max_digits=19, decimal_places=4)
]


class ProductCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: Sku
    name: ProductName
    unit: ProductUnit = "each"
    sale_price: ProductAmount
    cost: ProductAmount = Decimal("0")
    reorder_point: ProductAmount = Decimal("0")

    @field_validator("sku", "name", "unit", mode="before")
    @classmethod
    def _trim(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value


class ProductUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sale_price: ProductAmount | None = None
    cost: ProductAmount | None = None
    reorder_point: ProductAmount | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def _require_change(self) -> "ProductUpdate":
        if not self.model_fields_set or all(
            getattr(self, field) is None for field in self.model_fields_set
        ):
            raise ValueError("At least one non-null field must be supplied")
        return self


class ProductOut(BaseModel):
    id: uuid.UUID
    sku: str
    name: str
    unit: str
    current_sale_price: Decimal
    current_cost: Decimal
    reorder_point: Decimal
    is_active: bool
