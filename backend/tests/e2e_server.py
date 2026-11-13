"""Uvicorn entrypoint for the browser E2E smoke test.

The production app uses Redis for session revocation; the E2E job replaces it
with an in-process fakeredis instance so the test only needs PostgreSQL.
"""

from __future__ import annotations

import fakeredis.aioredis

from app.core import sessions

_fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
sessions._get_redis = lambda: _fake  # type: ignore[assignment]

from app.main import app  # noqa: E402,F401
