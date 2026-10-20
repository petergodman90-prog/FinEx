"""Customer model — deduplicated CRM record with encrypted PII."""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, LargeBinary, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.mixins import OrgScopedMixin, SoftDeleteMixin, TimestampMixin

# Deduplication: a partial unique index per org on each blind index, limited
# to live rows, so a soft-deleted customer never blocks re-creating a record
# with the same phone/email.


class Customer(Base, OrgScopedMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)

    email_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    email_bidx: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    phone_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    phone_bidx: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    company: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    source: Mapped[str] = mapped_column(
        String(40), nullable=False, default="manual", server_default=text("'manual'")
    )

    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        Index(
            "uq_customers_org_phone_bidx_active",
            "org_id",
            "phone_bidx",
            unique=True,
            postgresql_where=text("phone_bidx IS NOT NULL AND deleted_at IS NULL"),
        ),
        Index(
            "uq_customers_org_email_bidx_active",
            "org_id",
            "email_bidx",
            unique=True,
            postgresql_where=text("email_bidx IS NOT NULL AND deleted_at IS NULL"),
        ),
    )
