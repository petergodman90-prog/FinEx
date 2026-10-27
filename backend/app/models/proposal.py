"""Proposal model — staged writes awaiting human approval.

AI and integrations NEVER write to business tables directly; they create
proposals. Proposals expire (default 24h), are idempotent per org, and every
decision is audited.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.mixins import OrgScopedMixin, SoftDeleteMixin, TimestampMixin


class ProposalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    APPLIED = "applied"


class ProposalSource(StrEnum):
    AI = "ai"
    WHATSAPP = "whatsapp"
    MANUAL = "manual"
    SYSTEM = "system"


class Proposal(Base, OrgScopedMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "proposals"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    proposal_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)

    # Free-form staged payload; shape is defined by proposal_type upstream.
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)

    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="manual", server_default=text("'manual'")
    )
    status: Mapped[str] = mapped_column(
        Enum(*(s.value for s in ProposalStatus), name="proposal_status"),
        nullable=False,
        default=ProposalStatus.PENDING.value,
        server_default=text("'pending'"),
        index=True,
    )

    # Dedupe replays of the same proposal while it is still pending.
    idempotency_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Set only after the posting service has written the ledger entry. There is
    # deliberately no cross-schema FK: the ledger remains independently usable.
    journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    # Last posting failure: {code, message, retryable, details}, cleared on
    # success. A failed approval stays ``approved`` so it can be retried.
    posting_error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        Index(
            "uq_proposals_org_idem_pending",
            "org_id",
            "idempotency_hash",
            unique=True,
            postgresql_where=text("status = 'pending' AND deleted_at IS NULL"),
        ),
    )
