"""Membership model — user ↔ organization link carrying the role."""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Enum, ForeignKey, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.mixins import OrgScopedMixin, TimestampMixin
from app.core.rbac import Role

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
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    __table_args__ = (
        UniqueConstraint("org_id", "user_id", name="uq_memberships_org_user"),
    )
