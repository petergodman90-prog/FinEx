"""Membership model — user ↔ organization link carrying the role."""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.mixins import OrgScopedMixin, TimestampMixin
from app.core.rbac import Role


class MembershipStatus(StrEnum):
    """Lifecycle of a user's access to one organisation.

    ``pending`` rows are invitations created by an owner/admin for an
    existing FinEx account. They grant no access until the invitee accepts.
    """

    ACTIVE = "active"
    PENDING = "pending"
    SUSPENDED = "suspended"


# Tenant-scoped (RLS): the policy additionally allows a user to read their
# own memberships before an active org is known (see core/rls.py).
class Membership(Base, OrgScopedMixin, TimestampMixin):
    __tablename__ = "memberships"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(
        Enum(*(r.value for r in Role), name="membership_role"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        Enum(*(s.value for s in MembershipStatus), name="membership_status"),
        nullable=False,
        default=MembershipStatus.ACTIVE.value,
        server_default=text("'active'"),
        index=True,
    )
    # Display-only job title. Access control stays in ``role`` above.
    job_title: Mapped[str | None] = mapped_column(String(80), nullable=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("departments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    reports_to_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("memberships.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    __table_args__ = (
        UniqueConstraint("org_id", "user_id", name="uq_memberships_org_user"),
        CheckConstraint(
            "reports_to_id IS NULL OR reports_to_id <> id",
            name="ck_memberships_reports_to_not_self",
        ),
        Index("ix_memberships_org_status", "org_id", "status"),
    )
