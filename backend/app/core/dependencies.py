"""
FastAPI dependency injection factories.

get_current_user   — extracts and validates JWT from Authorization header.
get_rls_session    — DB session with RLS context (org/user/role) bound to the
                     transaction via ``set_config(..., is_local=true)``.
require_permission — factory for route-level permission checks.

Security invariants:
  1. Every tenant-scoped route MUST use ``get_rls_session``.
  2. Permission checks MUST use ``require_permission(...)``.
  3. Token revocation (per-token + per-user) is checked on every request.
"""

from __future__ import annotations

from typing import AsyncGenerator

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import context
from app.core.auth import TokenPayload, decode_access_token
from app.core.db import SessionLocal
from app.core.rbac import check_permission
from app.core.rls import bind_session_context
from app.core.sessions import is_token_revoked, is_user_revoked

_bearer = HTTPBearer(auto_error=True)


# ── Authentication ─────────────────────────────────────────


async def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
) -> TokenPayload:
    """Extract, decode, and validate the JWT bearer token."""
    payload = decode_access_token(creds.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    # Per-token revocation
    if await is_token_revoked(payload.jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
        )
    # Per-user emergency revocation (kill-switch)
    if await is_user_revoked(payload.sub):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="All sessions have been revoked for this user",
        )
    context.bind_request_context(
        user_id=payload.sub,
        org_id=payload.org_id,
        role=payload.role,
    )
    return payload


# ── RLS-Scoped Database Session ────────────────────────────


async def get_rls_session(
    user: TokenPayload = Depends(get_current_user),
) -> AsyncGenerator[AsyncSession, None]:
    """
    Yield an ``AsyncSession`` with PostgreSQL RLS context bound.

    ``set_config(..., is_local=true)`` is transaction-scoped; the
    ``session.begin()`` block keeps all route operations in one transaction
    and one tenant scope. Clean return → commit; exception → rollback.

    Services must ``flush()`` (never ``commit()``): transaction boundaries
    belong to this dependency.
    """
    async with SessionLocal() as session:
        async with session.begin():
            await bind_session_context(
                session,
                org_id=user.org_id,
                user_id=user.sub,
                role=user.role,
            )
            yield session


# ── Permission Enforcement ─────────────────────────────────


def require_permission(*permissions: str):
    """
    FastAPI dependency factory — raises 403 if the caller lacks
    ANY of the listed permissions.

    Usage::

        @router.get(
            "/items",
            dependencies=[Depends(require_permission("items:read"))],
        )
        async def list_items(...):
            ...
    """

    async def _check(user: TokenPayload = Depends(get_current_user)) -> None:
        for perm in permissions:
            if not check_permission(user.role, perm):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Insufficient permissions: requires '{perm}'",
                )

    return _check
