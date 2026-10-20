"""User model — global identity with encrypted PII."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, LargeBinary, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.mixins import SoftDeleteMixin, TimestampMixin

# NOTE: users are a *global* identity table (a user may belong to several
# organizations via memberships), so there is no org_id / RLS here. PII
# columns are Fernet-encrypted; ``*_bidx`` columns are HMAC blind indexes
# used for exact-match lookups without decryption.


class User(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)

    email_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    email_bidx: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )

    phone_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    phone_bidx: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
