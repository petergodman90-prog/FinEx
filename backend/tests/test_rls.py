"""RLS verification for every tenant table (security charter §8).

Structural checks: RLS enabled AND forced + at least one policy on every
table carrying an ``org_id`` column.

Behavioral checks (as the RLS-restricted ``finex_app`` role):
- rows of another org are invisible;
- inserting a row for another org raises (WITH CHECK);
- audit_logs is append-only (UPDATE/DELETE blocked by trigger).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import SQLAlchemyError

import app.models  # noqa: F401  — register all tables on Base.metadata
from app.core.auth import TokenPayload
from app.core.db import Base, SessionLocal
from app.core.rls import APP_DB_ROLE, bind_session_context, tenant_table_names
from app.models.organization import Organization
from app.schemas.auth import RegisterRequest
from app.schemas.customer import CustomerCreate
from app.services import auth_service, customer_service

TENANT_TABLES = tenant_table_names(Base.metadata)


def _future(hours: int = 24) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=hours)


# ── Graph: two orgs, two users, one customer in org A ─────


@pytest_asyncio.fixture
async def graph(tracker):
    async with SessionLocal() as session:
        reg = await auth_service.register(
            session,
            RegisterRequest(
                org_name="RLS Org A",
                full_name="Owner A",
                email=f"rls-a-{uuid.uuid4().hex[:10]}@example.com",
                password="password123",
            ),
        )
    org_a = reg.active_org_id
    user_a = reg.user.id

    # Org B has NO link to user_a — it exists purely as the "other tenant".
    async with SessionLocal() as session:
        org_b = Organization(
            name="RLS Org B", slug=f"rls-b-{uuid.uuid4().hex[:8]}"
        )
        session.add(org_b)
        await session.flush()
        user_b = await auth_service.create_user(
            session,
            email=f"rls-b-{uuid.uuid4().hex[:10]}@example.com",
            password="password123",
            full_name="Member B",
        )
        user_c = await auth_service.create_user(
            session,
            email=f"rls-c-{uuid.uuid4().hex[:10]}@example.com",
            password="password123",
            full_name="Unrelated C",
        )
        await auth_service.add_membership(
            session, user_id=user_b.id, org_id=org_b.id, role="viewer"
        )
        await bind_session_context(session, org_id=org_a, user_id=user_a, role="owner")
        actor = TokenPayload(
            sub=str(user_a), org_id=org_a, role="owner", jti="test", exp=_future()
        )
        customer = await customer_service.create_customer(
            session, CustomerCreate(name="Probe Customer"), actor
        )
        await session.commit()

    ids = {
        "org_a": org_a,
        "org_b": org_b.id,
        "user_a": user_a,
        "user_b": user_b.id,
        "user_c": user_c.id,
        "customer_a": customer.id,
    }
    tracker.add(str(org_a))
    tracker.add(str(org_b.id))
    return ids


# ── Structural checks ─────────────────────────────────────


@pytest.mark.parametrize("table_name", TENANT_TABLES)
async def test_rls_enabled_and_forced(table_name: str) -> None:
    async with SessionLocal() as session:
        row = (
            await session.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE oid = CAST(:t AS regclass)"
                ),
                {"t": f"public.{table_name}"},
            )
        ).one_or_none()
        assert row is not None, f"table {table_name} not found"
        assert row[0] is True, f"{table_name}: RLS not enabled"
        assert row[1] is True, f"{table_name}: RLS not FORCED"
        policies = await session.scalar(
            text(
                "SELECT count(*) FROM pg_policies "
                "WHERE schemaname = 'public' AND tablename = :t"
            ),
            {"t": table_name},
        )
        assert policies and policies >= 1, f"{table_name}: no RLS policy"


def _values_for(table_name: str, g: dict) -> dict:
    if table_name == "memberships":
        # Probe member is unrelated to both acting users: the memberships
        # policy intentionally lets a user read their OWN rows across orgs,
        # so the probe must not belong to user_a or user_b.
        return {"user_id": g["user_c"], "role": "viewer"}
    if table_name == "customers":
        return {"name": f"probe-{uuid.uuid4().hex[:6]}"}
    if table_name == "touchpoints":
        return {"customer_id": g["customer_a"], "kind": "note", "summary": "probe"}
    if table_name == "proposals":
        return {
            "proposal_type": "generic",
            "title": "probe",
            "payload": {},
            "idempotency_hash": uuid.uuid4().hex,
            "expires_at": _future(),
        }
    if table_name == "audit_logs":
        return {"action": f"probe-{uuid.uuid4().hex[:6]}"}
    raise AssertionError(f"no probe values defined for {table_name}")


# ── Behavioral checks ─────────────────────────────────────


@pytest.mark.parametrize("table_name", TENANT_TABLES)
async def test_tenant_isolation(table_name: str, graph: dict) -> None:
    table = Base.metadata.tables[table_name]

    # Seed one probe row for org A (owner connection; context bound anyway).
    async with SessionLocal() as session:
        await bind_session_context(
            session, org_id=graph["org_a"], user_id=graph["user_a"], role="owner"
        )
        await session.execute(
            insert(table).values(org_id=graph["org_a"], **_values_for(table_name, graph))
        )
        await session.commit()

    # Query as the restricted app role.
    async with SessionLocal() as session:
        await session.execute(text(f"SET ROLE {APP_DB_ROLE}"))
        try:
            await bind_session_context(
                session, org_id=graph["org_b"], user_id=graph["user_b"], role="viewer"
            )
            count_stmt = select(func.count()).select_from(table)
            if table_name == "memberships":
                # Only org A rows matter here: org B rows are legitimately
                # visible to org B's own user.
                count_stmt = count_stmt.where(table.c.org_id == graph["org_a"])
            visible_to_b = await session.scalar(count_stmt)
            assert visible_to_b == 0, f"{table_name}: org B can see org A rows"

            await bind_session_context(
                session, org_id=graph["org_a"], user_id=graph["user_a"], role="owner"
            )
            visible_to_a = await session.scalar(select(func.count()).select_from(table))
            assert visible_to_a >= 1, f"{table_name}: org A cannot see its own rows"

            # WITH CHECK: writing a row for another org must fail.
            with pytest.raises(SQLAlchemyError):
                await session.execute(
                    insert(table).values(
                        org_id=graph["org_b"], **_values_for(table_name, graph)
                    )
                )
                await session.flush()
            await session.rollback()
        finally:
            await session.rollback()
            await session.execute(text("RESET ROLE"))


async def test_audit_logs_append_only(graph: dict) -> None:
    marker = f"immutable-{uuid.uuid4().hex[:6]}"
    async with SessionLocal() as session:
        await bind_session_context(
            session, org_id=graph["org_a"], user_id=graph["user_a"], role="owner"
        )
        await session.execute(
            insert(Base.metadata.tables["audit_logs"]).values(
                org_id=graph["org_a"], action=marker
            )
        )
        await session.commit()

        with pytest.raises(SQLAlchemyError):
            await session.execute(
                text("UPDATE audit_logs SET action = 'tampered' WHERE action = :m"),
                {"m": marker},
            )
        await session.rollback()

        with pytest.raises(SQLAlchemyError):
            await session.execute(
                text("DELETE FROM audit_logs WHERE action = :m"), {"m": marker}
            )
        await session.rollback()
