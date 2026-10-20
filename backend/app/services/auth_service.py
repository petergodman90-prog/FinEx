"""
Authentication service — registration, login, refresh, org switching.

These flows run *before* (or around) an active org exists, so they bind the
RLS GUCs explicitly as soon as identity is known:

- user-only context → memberships with ``user_id = GUC_USER`` are readable
  (see the memberships policy override in core/rls.py);
- org context → audit rows and org-scoped reads/writes are valid.

The service owns its transaction boundaries (``commit()`` at the end of each
flow) because these endpoints use a plain DB session, not the RLS-scoped
request dependency.
"""

from __future__ import annotations

import re
import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import context
from app.core.auth import (
    TokenPayload,
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
)
from app.core.encryption import canonical_email, canonical_phone, get_pii_engine
from app.core.passwords import hash_password, verify_password
from app.core.rbac import Role
from app.core.rls import bind_session_context
from app.core.sessions import is_token_revoked, is_user_revoked, revoke_token
from app.models.membership import Membership
from app.models.organization import Organization
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    MembershipOut,
    MeResponse,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
)
from app.services import audit_service
from app.services.errors import (
    AuthenticationError,
    ConflictError,
    PermissionDeniedError,
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


# ── Helpers ────────────────────────────────────────────────


def _slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    return slug[:60] or "org"


def _user_out(user: User) -> UserOut:
    pii = get_pii_engine()
    return UserOut(
        id=user.id,
        full_name=user.full_name,
        email=pii.try_decrypt(user.email_encrypted),
        phone=pii.try_decrypt(user.phone_encrypted),
        created_at=user.created_at,
    )


async def _load_memberships(
    session: AsyncSession, user_id: uuid.UUID
) -> list[tuple[Membership, str]]:
    """Memberships + org names for a user (binds user context so the
    memberships RLS policy lets a user read their own rows)."""
    await bind_session_context(session, user_id=user_id)
    rows = (
        await session.execute(
            select(Membership, Organization.name)
            .join(Organization, Organization.id == Membership.org_id)
            .where(Membership.user_id == user_id)
            .order_by(Membership.created_at)
        )
    ).all()
    return [(m, name) for m, name in rows]


def _select_membership(
    memberships: list[tuple[Membership, str]], org_id: uuid.UUID | None
) -> tuple[Membership, str]:
    if not memberships:
        raise PermissionDeniedError("User has no organization membership")
    if org_id is not None:
        for m, name in memberships:
            if m.org_id == org_id:
                return m, name
        raise PermissionDeniedError("Not a member of the requested organization")
    for m, name in memberships:
        if m.is_default:
            return m, name
    return memberships[0]


def _membership_out(m: Membership, org_name: str) -> MembershipOut:
    return MembershipOut(
        org_id=m.org_id,
        org_name=org_name,
        role=m.role,
        is_default=m.is_default,
        joined_at=m.created_at,
    )


def _issue_tokens(
    user: User,
    memberships: list[tuple[Membership, str]],
    active: tuple[Membership, str],
    *,
    include_refresh: bool = True,
) -> TokenResponse:
    active_m, active_name = active
    access, _, access_exp = create_access_token(user.id, active_m.org_id, active_m.role)
    refresh = refresh_jti = refresh_exp = None
    if include_refresh:
        refresh, refresh_jti, refresh_exp = create_refresh_token(user.id)
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        access_expires_at=access_exp,
        refresh_expires_at=refresh_exp,
        active_org_id=active_m.org_id,
        role=active_m.role,
        user=_user_out(user),
        memberships=[_membership_out(m, n) for m, n in memberships],
    )


# ── User / org provisioning (used by register + staff management later) ──


async def create_user(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    full_name: str,
    phone: str | None = None,
) -> User:
    """Create a global user identity (no org membership). Flushes."""
    pii = get_pii_engine()
    email_c = canonical_email(email)
    email_bidx = pii.blind_index(email_c)
    exists = await session.scalar(select(User.id).where(User.email_bidx == email_bidx))
    if exists is not None:
        raise ConflictError("An account with this email already exists")

    phone_c = canonical_phone(phone) if phone else None
    user = User(
        full_name=full_name.strip(),
        email_encrypted=pii.encrypt(email_c),
        email_bidx=email_bidx,
        phone_encrypted=pii.encrypt(phone_c) if phone_c else None,
        phone_bidx=pii.blind_index(phone_c) if phone_c else None,
        password_hash=hash_password(password),
        is_active=True,
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:  # race on the unique email blind index
        raise ConflictError("An account with this email already exists") from exc
    return user


async def add_membership(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    org_id: uuid.UUID,
    role: str = Role.STAFF.value,
    is_default: bool = False,
) -> Membership:
    """Attach a user to an org with a role. Binds context first so the RLS
    ``WITH CHECK`` on memberships passes in hardened (non-superuser) setups."""
    await bind_session_context(session, org_id=org_id, user_id=user_id, role=role)
    membership = Membership(
        org_id=org_id, user_id=user_id, role=role, is_default=is_default
    )
    session.add(membership)
    await session.flush()
    return membership


async def create_org(
    session: AsyncSession, *, user_id: uuid.UUID, org_name: str, role: str = Role.OWNER.value
) -> tuple[Organization, Membership]:
    """Create an organization owned by ``user_id``. Returns (org, membership)."""
    org = Organization(name=org_name.strip(), slug=f"{_slugify(org_name)}-{secrets.token_hex(3)}")
    session.add(org)
    await session.flush()
    membership = await add_membership(
        session, user_id=user_id, org_id=org.id, role=role, is_default=True
    )
    return org, membership


# ── Public flows ───────────────────────────────────────────


async def register(session: AsyncSession, data: RegisterRequest) -> TokenResponse:
    """Signup: user + organization + owner membership, then auto-login."""
    user = await create_user(
        session,
        email=str(data.email),
        password=data.password,
        full_name=data.full_name,
        phone=data.phone,
    )
    org, membership = await create_org(
        session, user_id=user.id, org_name=data.org_name, role=Role.OWNER.value
    )
    context.bind_request_context(user_id=user.id, org_id=org.id, role=membership.role)
    await audit_service.record(
        session,
        action="auth.registered",
        entity_type="user",
        entity_id=user.id,
        after_state={"org_id": str(org.id), "role": membership.role},
    )
    memberships = await _load_memberships(session, user.id)
    active = _select_membership(memberships, org.id)
    response = _issue_tokens(user, memberships, active)
    await session.commit()
    return response


async def login(session: AsyncSession, data: LoginRequest) -> TokenResponse:
    pii = get_pii_engine()
    email_bidx = pii.blind_index(canonical_email(str(data.email)))
    user = await session.scalar(
        select(User).where(User.email_bidx == email_bidx, User.deleted_at.is_(None))
    )
    if (
        user is None
        or not user.is_active
        or not verify_password(data.password, user.password_hash)
    ):
        # Same message for unknown email / wrong password — no enumeration.
        raise AuthenticationError("Invalid email or password")

    memberships = await _load_memberships(session, user.id)
    active = _select_membership(memberships, data.org_id)

    user.last_login_at = datetime.now(timezone.utc)
    context.bind_request_context(user_id=user.id, org_id=active[0].org_id, role=active[0].role)
    await bind_session_context(
        session, org_id=active[0].org_id, user_id=user.id, role=active[0].role
    )
    await audit_service.record(
        session, action="auth.login", entity_type="user", entity_id=user.id
    )
    response = _issue_tokens(user, memberships, active)
    await session.commit()
    return response


async def refresh(session: AsyncSession, data: RefreshRequest) -> TokenResponse:
    payload = decode_refresh_token(data.refresh_token)
    if payload is None:
        raise AuthenticationError("Invalid or expired refresh token")
    if await is_token_revoked(payload.jti) or await is_user_revoked(payload.sub):
        raise AuthenticationError("Refresh token has been revoked")

    user = await session.get(User, uuid.UUID(payload.sub))
    if user is None or not user.is_active or user.deleted_at is not None:
        raise AuthenticationError("Account is not active")

    memberships = await _load_memberships(session, user.id)
    active = _select_membership(memberships, data.org_id)

    # Rotation: burn the presented refresh token before issuing a new one.
    await revoke_token(payload.jti, payload.exp)

    context.bind_request_context(user_id=user.id, org_id=active[0].org_id, role=active[0].role)
    response = _issue_tokens(user, memberships, active)
    await session.commit()
    return response


async def switch_org(
    session: AsyncSession, actor: TokenPayload, target_org_id: uuid.UUID
) -> TokenResponse:
    """Issue a fresh access token scoped to another org the user belongs to."""
    user = await session.get(User, uuid.UUID(actor.sub))
    if user is None or not user.is_active or user.deleted_at is not None:
        raise AuthenticationError("Account is not active")

    memberships = await _load_memberships(session, user.id)
    active = _select_membership(memberships, target_org_id)

    context.bind_request_context(user_id=user.id, org_id=active[0].org_id, role=active[0].role)
    await bind_session_context(
        session, org_id=active[0].org_id, user_id=user.id, role=active[0].role
    )
    await audit_service.record(
        session,
        action="auth.org_switched",
        entity_type="membership",
        entity_id=active[0].id,
        meta={"org_id": str(active[0].org_id)},
    )
    response = _issue_tokens(user, memberships, active, include_refresh=False)
    await session.commit()
    return response


async def logout(actor: TokenPayload, refresh_token: str | None = None) -> None:
    """Revoke the presented access token; optionally its refresh token too."""
    await revoke_token(actor.jti, actor.exp)
    if refresh_token:
        payload = decode_refresh_token(refresh_token)
        if payload is not None and payload.sub == actor.sub:
            await revoke_token(payload.jti, payload.exp)


async def me(session: AsyncSession, actor: TokenPayload) -> MeResponse:
    # NOTE: membership revocation mid-token-lifetime is handled by the Redis
    # kill-switch (core/sessions.revoke_all_user_tokens); per-membership
    # revocation arrives with staff management.
    user = await session.get(User, uuid.UUID(actor.sub))
    if user is None or not user.is_active or user.deleted_at is not None:
        raise AuthenticationError("Account is not active")
    memberships = await _load_memberships(session, user.id)
    active = _select_membership(memberships, actor.org_id)
    return MeResponse(
        user=_user_out(user),
        active_org_id=active[0].org_id,
        role=active[0].role,
        memberships=[_membership_out(m, n) for m, n in memberships],
    )
