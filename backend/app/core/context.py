"""
Request-scoped context (request_id, user_id, org_id, role).

Populated by ``RequestContextMiddleware`` (request_id) and by the
auth/RLS dependencies once the JWT is decoded. Consumed by the audit
service and structured logging, so helpers never have to thread these
values through every function call.

Never store raw PII here — IDs and roles only.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar, Token

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)
org_id_var: ContextVar[str | None] = ContextVar("org_id", default=None)
role_var: ContextVar[str | None] = ContextVar("role", default=None)


def new_request_id() -> str:
    return uuid.uuid4().hex


def bind_request_context(
    *,
    request_id: str | None = None,
    user_id: str | None = None,
    org_id: str | None = None,
    role: str | None = None,
) -> list[Token]:
    """Set any provided context values; returns tokens for ``reset_request_context``."""
    tokens: list[Token] = []
    if request_id is not None:
        tokens.append(request_id_var.set(request_id))
    if user_id is not None:
        tokens.append(user_id_var.set(str(user_id)))
    if org_id is not None:
        tokens.append(org_id_var.set(str(org_id)))
    if role is not None:
        tokens.append(role_var.set(str(role)))
    return tokens


def reset_request_context(tokens: list[Token]) -> None:
    for token in reversed(tokens):
        token.var.reset(token)


def current_request_id() -> str | None:
    return request_id_var.get()


def current_user_id() -> str | None:
    return user_id_var.get()


def current_org_id() -> str | None:
    return org_id_var.get()


def current_role() -> str | None:
    return role_var.get()
