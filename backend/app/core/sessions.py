"""
Redis-backed session / token revocation.

Supports instant token revocation by storing revoked JTI (JWT ID) values
in Redis with TTL matching the token's remaining lifetime.

Also supports per-user emergency revocation (kill-switch) that invalidates
ALL tokens for a given user.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from functools import lru_cache

import redis.asyncio as aioredis

_REVOKED_PREFIX = "finex:revoked:"
_USER_REVOKED_PREFIX = f"{_REVOKED_PREFIX}user:"


@lru_cache(maxsize=1)
def _get_redis() -> aioredis.Redis:
    from app.core.config import get_settings

    return aioredis.from_url(
        get_settings().REDIS_URL,
        decode_responses=True,
    )


# ── Per-Token Revocation ───────────────────────────────────


async def revoke_token(jti: str, expires_at: datetime) -> None:
    """Add a single token JTI to the revocation list (TTL = remaining lifetime)."""
    r = _get_redis()
    ttl = int((expires_at - datetime.now(timezone.utc)).total_seconds())
    if ttl > 0:
        await r.setex(f"{_REVOKED_PREFIX}{jti}", ttl, "1")


async def is_token_revoked(jti: str) -> bool:
    """Check whether a specific token has been revoked."""
    r = _get_redis()
    return await r.exists(f"{_REVOKED_PREFIX}{jti}") > 0


async def consume_token(jti: str, expires_at: datetime) -> bool:
    """Atomically burn a token JTI.

    Returns ``True`` only for the first caller. This is the primitive that
    makes refresh-token rotation race-safe: two concurrent refreshes using the
    same token cannot both succeed.
    """
    ttl = int((expires_at - datetime.now(timezone.utc)).total_seconds())
    if ttl <= 0:
        return False
    r = _get_redis()
    return bool(
        await r.set(
            f"{_REVOKED_PREFIX}{jti}",
            "1",
            ex=ttl,
            nx=True,
        )
    )


# ── Per-User Emergency Revocation ──────────────────────────


def _max_token_ttl_seconds() -> int:
    """Longest-lived token, with a small buffer for one-second JWT precision."""
    from app.core.config import get_settings

    settings = get_settings()
    return (
        max(
            settings.ACCESS_TOKEN_EXPIRE_MINUTES,
            settings.REFRESH_TOKEN_EXPIRE_MINUTES,
        )
        * 60
        + 1
    )


async def revoke_all_user_tokens(
    user_id: str, ttl_seconds: int | None = None
) -> None:
    """
    Mark ALL tokens issued at or before now as revoked (emergency kill-switch).

    The revocation timestamp is stored in the value, so a user who signs in
    again after the event receives usable tokens without waiting for the
    kill-switch TTL to expire. Refresh-token reuse also uses this primitive to
    invalidate the whole token family.

    The TTL defaults to the refresh-token lifetime; otherwise a revoked
    refresh token would become valid again after 24 hours.
    """
    if ttl_seconds is None:
        ttl_seconds = _max_token_ttl_seconds()
    if ttl_seconds <= 0:
        return
    revoked_at = int(time.time())
    r = _get_redis()
    await r.setex(
        f"{_USER_REVOKED_PREFIX}{user_id}",
        ttl_seconds,
        str(revoked_at),
    )


async def is_user_revoked(
    user_id: str, issued_at: datetime | None = None
) -> bool:
    """Check whether a user's kill-switch covers the token's issue time.

    Without ``issued_at`` the caller only wants to know whether the switch is
    active (used by tests and administrative checks). When a token issue time
    is supplied, tokens issued strictly after the revocation event remain
    valid, which lets a user sign back in immediately after an emergency
    revocation.
    """
    r = _get_redis()
    value = await r.get(f"{_USER_REVOKED_PREFIX}{user_id}")
    if value is None:
        return False
    if issued_at is None:
        return True
    try:
        revoked_at = float(value)
    except (TypeError, ValueError):
        # Legacy boolean value: fail closed.
        return True
    return issued_at.timestamp() <= revoked_at
