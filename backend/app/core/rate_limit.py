"""Redis-backed rate limiting for unauthenticated auth endpoints.

The limiter is intentionally fail-closed: if Redis is unavailable, login,
registration and refresh are refused rather than allowing an attacker to
brute-force credentials while the shared revocation store is down.

Keys contain a SHA-256 digest of the subject (client IP or normalized email)
so raw account identifiers are not stored in Redis. The counter and its
window are established in one Redis transaction.
"""

from __future__ import annotations

import hashlib

from fastapi import HTTPException, Request, status
from redis.exceptions import RedisError

from app.core import sessions
from app.core.config import get_settings

_PREFIX = "finex:rate:"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _client_subject(request: Request) -> str:
    # Uvicorn's proxy-header handling (or the reverse proxy) supplies the real
    # client address in production. Do not trust X-Forwarded-For directly here.
    if request.client is None:
        return "unknown"
    return request.client.host or "unknown"


def _too_many(retry_after: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many requests. Please try again later.",
        headers={"Retry-After": str(max(retry_after, 1))},
    )


async def _check(
    *,
    bucket: str,
    subject: str,
    limit: int,
    window_seconds: int,
) -> None:
    settings = get_settings()
    if not settings.AUTH_RATE_LIMIT_ENABLED:
        return
    if limit <= 0 or window_seconds <= 0:
        return

    key = f"{_PREFIX}{bucket}:{_digest(subject)}"
    try:
        redis = sessions._get_redis()
        # SET NX establishes the window on the first request; INCR then
        # counts it. Both run in one transaction, so a crash cannot leave a
        # counter without an expiry.
        async with redis.pipeline(transaction=True) as pipe:
            pipe.set(key, 0, ex=window_seconds, nx=True)
            pipe.incr(key)
            _, current = await pipe.execute()
        if int(current) > limit:
            ttl = int(await redis.ttl(key))
            if ttl < 1:
                # Repair a legacy/manual counter without an expiry so a
                # blocked subject cannot stay blocked forever.
                await redis.expire(key, window_seconds)
                ttl = window_seconds
            raise _too_many(ttl)
    except HTTPException:
        raise
    except (RedisError, OSError, ValueError) as exc:
        # Fail closed: auth must not proceed without shared rate-limit and
        # revocation state.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is temporarily unavailable.",
        ) from exc


async def enforce_login_rate_limit(request: Request, email: str) -> None:
    """Limit password attempts per client IP and per account."""
    settings = get_settings()
    await _check(
        bucket="login-ip",
        subject=_client_subject(request),
        limit=settings.LOGIN_RATE_LIMIT_PER_MINUTE,
        window_seconds=60,
    )
    await _check(
        bucket="login-account",
        subject=email.strip().lower(),
        limit=settings.LOGIN_ACCOUNT_RATE_LIMIT_PER_MINUTE,
        window_seconds=60,
    )


async def enforce_register_rate_limit(request: Request) -> None:
    settings = get_settings()
    await _check(
        bucket="register-ip",
        subject=_client_subject(request),
        limit=settings.REGISTER_RATE_LIMIT_PER_HOUR,
        window_seconds=3600,
    )


async def enforce_refresh_rate_limit(request: Request) -> None:
    settings = get_settings()
    await _check(
        bucket="refresh-ip",
        subject=_client_subject(request),
        limit=settings.REFRESH_RATE_LIMIT_PER_MINUTE,
        window_seconds=60,
    )
