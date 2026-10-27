"""Custom staff role model — display-only job titles per organisation."""

from __future__ import annotations

import uuid

from sqlalchemy import Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.mixins import OrgScopedMixin, TimestampMixin


class StaffRole(Base, OrgScopedMixin, TimestampMixin):
    """A user-defined job title.

    These are *not* RBAC roles. Access control lives on ``Membership.role``;
    deleting a staff role never changes anyone's permissions.
    """

    __tablename__ = "staff_roles"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    description: Mapped[str | None] = mapped_column(String(240), nullable=True)

    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_staff_roles_org_name"),
        Index(
            "uq_staff_roles_org_name_ci",
            "org_id",
            text("lower(name)"),
            unique=True,
        ),
    )
