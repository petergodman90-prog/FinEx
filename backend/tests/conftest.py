"""Shared test fixtures.

Redis is replaced with fakeredis so auth/session tests are hermetic.
The database is expected to be reachable via DATABASE_URL (docker compose);
the schema is migrated to head once per session.

On Windows the DB sockets are additionally pinned to rotating explicit local
ports: this machine's ephemeral-port allocator sporadically hands out a wedged
port (WSAEADDRINUSE / 10048 waves) — see ``_pin_db_connection_ports``.
"""

from __future__ import annotations

import asyncio
import os
import random
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _env_value(key: str) -> str | None:
    """Settings value for ``key``: environment first, then .env files.

    Mirrors ``Settings.model_config``'s env_file candidates (".env",
    "../.env") as resolved from the usual run directory (backend/).
    """
    if value := os.environ.get(key):
        return value
    for candidate in (BACKEND_DIR.parent / ".env", BACKEND_DIR / ".env"):
        if not candidate.exists():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(f"{key}="):
                return stripped.split("=", 1)[1].strip()
    return None


def _db_targets() -> set[tuple[str, int]]:
    """(host, port) pairs the suite talks to; scopes the socket pinning."""
    targets: set[tuple[str, int]] = set()
    for key in ("DATABASE_URL", "MIGRATION_DATABASE_URL"):
        url = _env_value(key)
        if url and (parts := urlsplit(url)).hostname:
            targets.add((parts.hostname, parts.port or 5432))
    return targets


# Windows' ephemeral-port allocator on this machine intermittently picks a
# local port that is already in use or in TIME_WAIT (classically 49168, the
# first port of the dynamic range), so every connect fails with
# WSAEADDRINUSE/10048 until the allocator's state clears. Binding an explicit
# source port that we rotate ourselves sidesteps the allocator entirely: the
# only thing we can collide with is our own recent connections, and retrying
# on the next rotation step dodges their TIME_WAIT.
_PINNED_PORT_BASE = 30100
_PINNED_PORT_POOL = 500
_PINNED_PORT_TRIES = 64
_port_cursor = random.randrange(_PINNED_PORT_POOL)
_DB_TARGETS: set[tuple[str, int]] = set()


def _next_pinned_port() -> int:
    global _port_cursor
    port = _PINNED_PORT_BASE + _port_cursor
    _port_cursor = (_port_cursor + 1) % _PINNED_PORT_POOL
    return port


def _pin_db_connection_ports(loop: asyncio.AbstractEventLoop) -> None:
    """Give DB connections explicit rotating local ports (Windows only).

    Wraps ``loop.create_connection`` for the configured DB host/port pairs
    (set ``FINEX_DISABLE_PORT_PINNING=1`` to opt out). Anything that is not a
    DB connection is passed through untouched.
    """
    if not _DB_TARGETS:
        return
    orig = loop.create_connection

    async def create_connection(protocol_factory, host=None, port=None, **kwargs):
        if (
            isinstance(host, str)
            and (host, port) in _DB_TARGETS
            and "local_addr" not in kwargs
        ):
            bind_ip = "127.0.0.1" if host in {"127.0.0.1", "localhost"} else "0.0.0.0"
            last_error: OSError | None = None
            for _ in range(_PINNED_PORT_TRIES):
                try:
                    return await orig(
                        protocol_factory,
                        host,
                        port,
                        local_addr=(bind_ip, _next_pinned_port()),
                        **kwargs,
                    )
                except OSError as exc:
                    errno = exc.errno or getattr(exc, "winerror", None)
                    if errno not in (10048, 10049):  # ADDRINUSE / ADDRNOTAVAIL
                        raise
                    last_error = exc
            assert last_error is not None
            raise last_error
        return await orig(protocol_factory, host, port, **kwargs)

    loop.create_connection = create_connection  # type: ignore[method-assign]


if sys.platform == "win32":
    # The selector loop is rock solid for TCP sockets (the Proactor loop
    # sporadically fails asyncpg connects with WinError 52). The policy also
    # installs the port-pinning hook on every loop it creates: pytest-asyncio's
    # session loop and alembic's asyncio.run both go through the policy.
    _DB_TARGETS = _db_targets()
    if os.environ.get("FINEX_DISABLE_PORT_PINNING") == "1":
        _DB_TARGETS = set()

    class _PinnedPortsSelectorEventLoopPolicy(asyncio.WindowsSelectorEventLoopPolicy):
        def new_event_loop(self):
            loop = super().new_event_loop()
            _pin_db_connection_ports(loop)
            return loop

    asyncio.set_event_loop_policy(_PinnedPortsSelectorEventLoopPolicy())

import fakeredis.aioredis
import pytest
import pytest_asyncio
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.exc import OperationalError

from app.core.auth import create_access_token
from app.core.db import SessionLocal
from app.core.rls import bind_session_context, enable_audit_purge
from app.models.organization import Organization
from app.services import auth_service

DEFAULT_PASSWORD = "password123"


@pytest.fixture(scope="session", autouse=True)
def migrated_db():
    """Ensure the schema is at head before any test runs.

    DB sockets on Windows are pinned to rotating local ports at the loop
    level (see ``_pin_db_connection_ports``); the retries below cover any
    transient blip that still gets through.
    """
    cfg = AlembicConfig(str(BACKEND_DIR / "alembic.ini"))
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            alembic_command.upgrade(cfg, "head")
            return
        except (OSError, ConnectionError, OperationalError) as exc:  # transient proxy failures
            last_error = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("database unreachable for alembic upgrade") from last_error


@pytest_asyncio.fixture(autouse=True)
async def fake_redis(monkeypatch):
    """Replace the Redis client with an in-memory fake."""
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.core.sessions._get_redis", lambda: fake)
    yield fake
    await fake.aclose()


@pytest_asyncio.fixture
async def client():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def tracker():
    """Collects created org ids and deletes them (cascades) at teardown."""
    state: set[str] = set()
    yield state
    if not state:
        return
    # One session for the whole cleanup pass: fewer sockets, fewer chances of
    # tripping Docker Desktop's port proxy.
    async with SessionLocal() as session:
        for org_id in state:
            await bind_session_context(session, org_id=org_id)
            # Org teardown must remove its audit trail; the append-only
            # trigger requires the explicit, sanctioned purge flag.
            await enable_audit_purge(session)
            await session.execute(
                delete(Organization).where(Organization.id == uuid.UUID(org_id))
            )
            await session.commit()


@pytest_asyncio.fixture
async def register_user(client, tracker):
    """Register a fresh user+org through the API; returns the token payload."""

    async def _register(
        *,
        org_name: str = "Test Org",
        full_name: str = "Test User",
        email: str | None = None,
        password: str = DEFAULT_PASSWORD,
    ) -> dict:
        email = email or f"user-{uuid.uuid4().hex[:12]}@example.com"
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "org_name": org_name,
                "full_name": full_name,
                "email": email,
                "password": password,
            },
        )
        assert response.status_code == 201, response.text
        data = response.json()
        tracker.add(data["active_org_id"])
        return data

    return _register


@pytest_asyncio.fixture
async def make_member():
    """Create a user with a custom role inside an existing org + access token."""

    async def _make(org_id: str, role: str = "staff") -> dict:
        async with SessionLocal() as session:
            user = await auth_service.create_user(
                session,
                email=f"member-{uuid.uuid4().hex[:12]}@example.com",
                password=DEFAULT_PASSWORD,
                full_name="Test Member",
            )
            await auth_service.add_membership(
                session, user_id=user.id, org_id=uuid.UUID(str(org_id)), role=role
            )
            await session.commit()
            token, _jti, _exp = create_access_token(
                user.id, uuid.UUID(str(org_id)), role
            )
            return {"user_id": str(user.id), "token": token, "role": role}

    return _make


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
