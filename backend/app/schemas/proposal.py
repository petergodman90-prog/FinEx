"""Proposal schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.models.proposal import ProposalSource

TypeStr = Annotated[str, StringConstraints(min_length=1, max_length=50)]
TitleStr = Annotated[str, StringConstraints(min_length=1, max_length=200)]


class ProposalCreate(BaseModel):
    proposal_type: TypeStr
    title: TitleStr
    payload: dict = Field(default_factory=dict)
    amount: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    currency: Annotated[str, StringConstraints(min_length=3, max_length=3)] | None = None
    source: str = ProposalSource.MANUAL.value
    # Default 24h; hard cap of 72h (see security charter §3).
    expires_in_hours: int = Field(default=24, ge=1, le=72)
    # Optional client-supplied key strengthening the idempotency hash.
    idempotency_key: Annotated[str, StringConstraints(max_length=128)] | None = None


class ProposalDecision(BaseModel):
    note: Annotated[str, StringConstraints(max_length=2_000)] | None = None


class ProposalOut(BaseModel):
    id: uuid.UUID
    proposal_type: str
    title: str
    payload: dict
    amount: Decimal | None = None
    currency: str | None = None
    source: str
    status: str
    expires_at: datetime
    created_by_id: uuid.UUID | None = None
    decided_by_id: uuid.UUID | None = None
    decided_at: datetime | None = None
    decision_note: str | None = None
    journal_entry_id: uuid.UUID | None = None
    posting_error: dict | None = None
    created_at: datetime
    updated_at: datetime


class ProposalListOut(BaseModel):
    """Create response — ``created=False`` signals an idempotent replay."""

    proposal: ProposalOut
    created: bool
