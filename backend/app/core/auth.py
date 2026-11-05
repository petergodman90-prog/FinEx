"""
JWT token issuance and verification.

Access tokens carry:  user_id, active_org_id, role, jti.
Refresh tokens carry: user_id, jti, type="refresh".

Each token has a unique `jti` (JWT ID) that enables per-token revocation
via the sessions module.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from pydantic import BaseModel

from app.core.config import get_settings


# ── Token Payload Schemas ──────────────────────────────────


class TokenPayload(BaseModel):
    """Decoded access-token claims available to every route handler."""

    sub: str  # user_id
    org_id: uuid.UUID  # active_org_id
    role: str  # role in active org
    jti: str  # unique token ID for revocation
    iat: datetime  # issued-at; used by the per-user kill-switch
    exp: datetime


class RefreshPayload(BaseModel):
    """Decoded refresh-token claims."""

    sub: str
    jti: str
    token_type: str
    iat: datetime
    exp: datetime


# ── Token Creation ─────────────────────────────────────────


def create_access_token(
    user_id: uuid.UUID,
    org_id: uuid.UUID,
    role: str,
) -> tuple[str, str, datetime]:
    """
    Issue a signed access token.

    Returns ``(encoded_token, jti, expires_at)``.
    """
    settings = get_settings()
    jti = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES,
    )
    payload = {
        "sub": str(user_id),
        "org": str(org_id),
        "role": role,
        "jti": jti,
        "exp": expires_at,
        "iat": datetime.now(timezone.utc),
        "type": "access",
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return token, jti, expires_at


def create_refresh_token(user_id: uuid.UUID) -> tuple[str, str, datetime]:
    """
    Issue a signed refresh token.

    Returns ``(encoded_token, jti, expires_at)``.
    """
    settings = get_settings()
    jti = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=settings.REFRESH_TOKEN_EXPIRE_MINUTES,
    )
    payload = {
        "sub": str(user_id),
        "jti": jti,
        "exp": expires_at,
        "iat": datetime.now(timezone.utc),
        "type": "refresh",
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return token, jti, expires_at


# ── Token Decoding ─────────────────────────────────────────


def decode_access_token(token: str) -> Optional[TokenPayload]:
    """Decode and validate an access token.  Returns ``None`` on failure."""
    settings = get_settings()
    try:
        data = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
        if data.get("type") != "access":
            return None
        return TokenPayload(
            sub=data["sub"],
            org_id=uuid.UUID(data["org"]),
            role=data["role"],
            jti=data["jti"],
            iat=data["iat"],
            exp=data["exp"],
        )
    except (JWTError, KeyError, ValueError, TypeError):
        return None


def decode_refresh_token(token: str) -> Optional[RefreshPayload]:
    """Decode and validate a refresh token.  Returns ``None`` on failure."""
    settings = get_settings()
    try:
        data = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
        if data.get("type") != "refresh":
            return None
        return RefreshPayload(
            sub=data["sub"],
            jti=data["jti"],
            token_type=data["type"],
            iat=data["iat"],
            exp=data["exp"],
        )
    except (JWTError, KeyError, ValueError, TypeError):
        return None
