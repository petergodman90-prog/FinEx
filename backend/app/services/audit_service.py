"""Audit trail writer.

Every mutation in the platform goes through ``record()``. Snapshots are
defensively redacted: any dict key that looks like a PII/secret field is
replaced before it ever reaches the (immutable) audit table.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import context
from app.models.audit_log import AuditLog

_REDACTED = "***REDACTED***"
_PII_KEYS = {
    "email",
    "phone",
    "password",
    "password_hash",
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "api_key",
    "email_encrypted",
    "phone_encrypted",
}


def redact(value, _depth: int = 0):
    """Recursively strip PII/secret fields from audit snapshots."""
    if _depth > 6:
        return "***TRUNCATED***"
    if isinstance(value, dict):
        return {
            k: (_REDACTED if str(k).lower() in _PII_KEYS else redact(v, _depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v, _depth + 1) for v in value]
    if isinstance(value, (uuid.UUID,)):
        return str(value)
    if isinstance(value, (Decimal,)):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _as_uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


async def record(
    session: AsyncSession,
    *,
    action: str,
    entity_type: str | None = None,
    entity_id: str | uuid.UUID | None = None,
    before_state: dict | None = None,
    after_state: dict | None = None,
    meta: dict | None = None,
    org_id: str | uuid.UUID | None = None,
    actor_user_id: str | uuid.UUID | None = None,
    actor_role: str | None = None,
    request_id: str | None = None,
) -> AuditLog:
    """
    Append an audit row (flush only — the caller's transaction commits).

    Identity defaults are pulled from the request context so callers never
    have to thread request metadata through their signatures.
    """
    log = AuditLog(
        org_id=_as_uuid(org_id or context.current_org_id()),
        actor_user_id=_as_uuid(actor_user_id or context.current_user_id()),
        actor_role=actor_role or context.current_role(),
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        before_state=redact(before_state) if before_state is not None else None,
        after_state=redact(after_state) if after_state is not None else None,
        meta=redact(meta) if meta is not None else None,
        request_id=request_id or context.current_request_id(),
    )
    session.add(log)
    await session.flush()
    return log
