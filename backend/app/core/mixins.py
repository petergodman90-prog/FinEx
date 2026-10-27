"""
Reusable SQLAlchemy model mixins for FINEX.

OrgScopedMixin  — adds org_id FK + marks table for RLS policy generation.
TimestampMixin  — created_at / updated_at with server-side defaults.
SoftDeleteMixin — deleted_at for 30-day soft-delete lifecycle.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, declared_attr
from sqlalchemy.sql import func


class TimestampMixin:
    """Adds created_at and updated_at with server-side defaults."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SoftDeleteMixin:
    """
    Adds deleted_at for soft-delete lifecycle.

    Retention policy: 30 days after soft-delete → purge or crypto-shred.
    Query helpers should filter WHERE deleted_at IS NULL by default.
    """

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        default=None,
        nullable=True,
        index=True,
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class OrgScopedMixin:
    """
    Marks a table as tenant-scoped and adds the org_id FK.

    Every model using this mixin:
    - Gets an org_id column with FK to organizations.
    - Is registered for RLS policy generation in migrations.
    - Must be accessed through RLS-enabled DB sessions only.
    """

    __rls_enabled__: bool = True

    @declared_attr
    def org_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            PG_UUID(as_uuid=True),
            ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
