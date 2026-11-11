"""Supplier schemas for payable counterparties."""

from __future__ import annotations

import uuid
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    StringConstraints,
    field_validator,
)

SupplierName = Annotated[str, StringConstraints(min_length=1, max_length=200)]
SupplierCode = Annotated[str, StringConstraints(max_length=50)]


class SupplierCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: SupplierName
    code: SupplierCode | None = None

    @field_validator("name", "code", mode="before")
    @classmethod
    def _trim(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class SupplierOut(BaseModel):
    id: uuid.UUID
    code: str | None = None
    name: str
    is_active: bool = True
