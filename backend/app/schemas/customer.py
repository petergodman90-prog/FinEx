"""Customer & touchpoint schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.schemas.auth import EmailStr, PhoneStr

NameStr = Annotated[str, StringConstraints(min_length=1, max_length=200)]
TagList = Annotated[list[Annotated[str, StringConstraints(max_length=50)]], Field(max_length=20)]


class CustomerCreate(BaseModel):
    name: NameStr
    email: EmailStr | None = None
    phone: PhoneStr | None = None
    company: Annotated[str, StringConstraints(max_length=200)] | None = None
    notes: Annotated[str, StringConstraints(max_length=10_000)] | None = None
    tags: TagList = Field(default_factory=list)
    source: Annotated[str, StringConstraints(max_length=40)] = "manual"


class CustomerUpdate(BaseModel):
    """Partial update — only provided fields are applied (exclude_unset)."""

    name: NameStr | None = None
    email: EmailStr | None = None
    phone: PhoneStr | None = None
    company: Annotated[str, StringConstraints(max_length=200)] | None = None
    notes: Annotated[str, StringConstraints(max_length=10_000)] | None = None
    tags: TagList | None = None


class CustomerMergeRequest(BaseModel):
    target_id: uuid.UUID


class CustomerOut(BaseModel):
    id: uuid.UUID
    name: str
    email: str | None = None
    phone: str | None = None
    company: str | None = None
    notes: str | None = None
    tags: list[str] = Field(default_factory=list)
    source: str
    merged_into_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


class TouchpointCreate(BaseModel):
    kind: Annotated[str, StringConstraints(min_length=1, max_length=40)]
    summary: Annotated[str, StringConstraints(min_length=1, max_length=10_000)]
    occurred_at: datetime | None = None
    ref_type: Annotated[str, StringConstraints(max_length=50)] | None = None
    ref_id: Annotated[str, StringConstraints(max_length=64)] | None = None
    meta: dict = Field(default_factory=dict)


class TouchpointOut(BaseModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    kind: str
    summary: str
    ref_type: str | None = None
    ref_id: str | None = None
    meta: dict = Field(default_factory=dict)
    occurred_at: datetime
    created_by_id: uuid.UUID | None = None
    created_at: datetime
