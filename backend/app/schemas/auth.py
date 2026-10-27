"""Auth request/response schemas."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field, StringConstraints

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_email(value: str) -> str:
    value = value.strip().lower()
    if not _EMAIL_RE.match(value):
        raise ValueError("invalid email address")
    return value


EmailStr = Annotated[
    str, StringConstraints(max_length=254), AfterValidator(_validate_email)
]
PasswordStr = Annotated[str, StringConstraints(min_length=8, max_length=72)]
PhoneStr = Annotated[str, StringConstraints(max_length=32)]


class RegisterRequest(BaseModel):
    """Self-serve signup: creates the user, a new organization, and an owner membership."""

    org_name: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    full_name: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    email: EmailStr
    password: PasswordStr
    phone: PhoneStr | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: Annotated[str, StringConstraints(min_length=1, max_length=72)]
    org_id: uuid.UUID | None = None  # pick a specific org; default = primary membership


class RefreshRequest(BaseModel):
    refresh_token: str
    org_id: uuid.UUID | None = None  # active org for the new access token


class RefreshSessionRequest(BaseModel):
    """Browser refresh: the token itself comes only from the HttpOnly cookie."""

    org_id: uuid.UUID | None = None


class SwitchOrgRequest(BaseModel):
    org_id: uuid.UUID


class LogoutRequest(BaseModel):
    # Optional: also revoke the refresh token so the whole session dies.
    refresh_token: str | None = None


class MembershipOut(BaseModel):
    org_id: uuid.UUID
    org_name: str
    role: str
    is_default: bool
    joined_at: datetime


class UserOut(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str | None = None  # decrypted for the authenticated user only
    phone: str | None = None
    created_at: datetime


class SessionResponse(BaseModel):
    """Browser-facing response: tokens live only in HttpOnly cookies."""

    token_type: str = "bearer"
    access_expires_at: datetime
    refresh_expires_at: datetime | None = None
    active_org_id: uuid.UUID
    role: str
    user: UserOut
    memberships: list[MembershipOut] = Field(default_factory=list)


class TokenResponse(SessionResponse):
    """Explicit token response for non-browser API clients."""

    access_token: str
    refresh_token: str | None = None

    def session(self) -> SessionResponse:
        """Return the token-free browser representation."""
        return SessionResponse(
            **self.model_dump(exclude={"access_token", "refresh_token"})
        )


class MeResponse(BaseModel):
    user: UserOut
    active_org_id: uuid.UUID
    role: str
    memberships: list[MembershipOut]
