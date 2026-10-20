"""
Pure-ASGI middleware establishing per-request context.

Responsibilities:
- Assign a request id (honouring an inbound ``X-Request-ID`` header) and
  expose it on ``request.state`` + contextvars for audit logging.
- Guarantee contextvars are reset when the request finishes.

It deliberately does NOT decode JWTs or touch the database: the
transaction-scoped Postgres GUCs (org/user/role) are bound by
``get_rls_session`` where a real DB transaction exists (``SET LOCAL``
semantics), and FastAPI dependencies own authentication.
"""

from __future__ import annotations

from app.core.context import bind_request_context, reset_request_context

_MAX_REQUEST_ID_LEN = 64


def _clean_request_id(raw: bytes | None) -> str | None:
    if not raw:
        return None
    try:
        value = raw.decode("ascii")
    except UnicodeDecodeError:
        return None
    value = "".join(ch for ch in value if ch.isprintable()).strip()
    return value[:_MAX_REQUEST_ID_LEN] or None


class RequestContextMiddleware:
    def __init__(self, app) -> None:  # app: ASGI application
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        request_id = _clean_request_id(headers.get(b"x-request-id"))
        if request_id is None:
            from app.core.context import new_request_id

            request_id = new_request_id()

        scope.setdefault("state", {})["request_id"] = request_id
        tokens = bind_request_context(request_id=request_id)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_request_context(tokens)
