"""Organization (tenant root) model."""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.mixins import TimestampMixin


class Organization(Base, TimestampMixin):
    """
    A tenant.

    This table is the tenancy root — it is *not* org-scoped itself. Access to
    an organization row is always mediated by the caller's membership and
    role (see ``services/auth_service``).
    """

    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(
        String(80), nullable=False, unique=True, index=True
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="USD", server_default=text("'USD'")
    )
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="UTC", server_default=text("'UTC'")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    settings: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
