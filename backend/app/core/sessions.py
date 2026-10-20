"""
Redis-backed session / token revocation.

Supports instant token revocation by storing revoked JTI (JWT ID) values
in Redis with TTL matching the token's remaining lifetime.

Also supports per-user emergency revocation (kill-switch) that invalidates
ALL tokens for a given user.
"""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache

import redis.asyncio as aioredis

_REVOKED_PREFIX = "finex:revoked:"


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


# ── Per-User Emergency Revocation ──────────────────────────


async def revoke_all_user_tokens(user_id: str, ttl_seconds: int = 86400) -> None:
    """
    Mark ALL current tokens for a user as revoked (emergency kill-switch).

    The TTL should be at least as long as the longest-lived token
    (default 24 h covers both access and refresh tokens).
    """
    r = _get_redis()
    await r.setex(f"{_REVOKED_PREFIX}user:{user_id}", ttl_seconds, "1")


async def is_user_revoked(user_id: str) -> bool:
    """Check whether the per-user kill-switch is active."""
    r = _get_redis()
    return await r.exists(f"{_REVOKED_PREFIX}user:{user_id}") > 0
