"""Tenant isolation for Person A's financial ledger (migration 002).

The ledger schema is created from Person A's SQL but the tenant boundary is
Person B's responsibility.  These tests prove:

* every ledger table is RLS-enabled, forced and policied;
* the read views are ``security_invoker`` (no owner-privilege bypass);
* the restricted ``finex_app`` role can read only its org and cannot write
  tables directly, but can execute the posting functions;
* ``receipt_lines`` (which has no ``org_id``) is isolated through its receipt;
* a caller bound to org B cannot post into org A through the SECURITY DEFINER
  functions;
* posting is idempotent — the same key never creates a second entry.
"""

from __future__ import annotations

import json
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.db import SessionLocal
from app.core.rls import APP_DB_ROLE, bind_session_context
from app.models.organization import Organization
from app.schemas.auth import RegisterRequest
from app.services import auth_service

LEDGER_TENANT_TABLES = [
    "accounts",
    "accounting_periods",
    "org_sequences",
    "posting_events",
    "journal_entries",
    "journal_lines",
    "products",
    "stock_movements",
    "open_items",
    "payment_allocations",
    "receipts",
    "receipt_lines",
]

LEDGER_VIEWS = [
    "v_trial_balance",
    "v_inventory_on_hand",
    "v_open_item_balances",
]

IMMUTABLE_TABLES = [
    "journal_entries",
    "journal_lines",
    "stock_movements",
    "posting_events",
    "receipts",
    "receipt_lines",
    "open_items",
    "payment_allocations",
]


def _lines(account_a: uuid.UUID, account_b: uuid.UUID, amount: int) -> str:
    return json.dumps(
        [
            {"account_id": str(account_a), "debit": amount, "credit": 0},
            {"account_id": str(account_b), "debit": 0, "credit": amount},
        ]
    )


async def _insert_accounts(session, org_id, accounts: list[dict]) -> None:
    for account in accounts:
        await session.execute(
            text(
                "INSERT INTO ledger.accounts "
                "(id, org_id, code, name, account_type) "
                "VALUES (:id, :org, :code, :name, CAST(:kind AS ledger.account_type))"
            ),
            {
                "id": account["id"],
                "org": org_id,
                "code": account["code"],
                "name": account["name"],
                "kind": account["kind"],
            },
        )


async def _purge_ledger_for_orgs(org_ids: list[uuid.UUID]) -> None:
    """Remove ledger rows for test orgs, then let the tracker delete orgs.

    Financial rows are append-only by trigger, so tests (which own the tables)
    temporarily disable user triggers.  This is test-only maintenance, never
    exposed through the application role.
    """
    async with SessionLocal() as session:
        for table in IMMUTABLE_TABLES:
            await session.execute(text(f"ALTER TABLE ledger.{table} DISABLE TRIGGER USER"))
        try:
            for org in org_ids:
                params = {"org": org}
                await session.execute(
                    text("DELETE FROM ledger.payment_allocations WHERE org_id = :org"),
                    params,
                )
                await session.execute(
                    text(
                        "DELETE FROM ledger.receipt_lines WHERE receipt_id IN "
                        "(SELECT id FROM ledger.receipts WHERE org_id = :org)"
                    ),
                    params,
                )
                await session.execute(
                    text("DELETE FROM ledger.receipts WHERE org_id = :org"), params
                )
                await session.execute(
                    text("DELETE FROM ledger.open_items WHERE org_id = :org"), params
                )
                await session.execute(
                    text("DELETE FROM ledger.stock_movements WHERE org_id = :org"), params
                )
                await session.execute(
                    text("DELETE FROM ledger.journal_lines WHERE org_id = :org"), params
                )
                await session.execute(
                    text("DELETE FROM ledger.journal_entries WHERE org_id = :org"), params
                )
                await session.execute(
                    text("DELETE FROM ledger.posting_events WHERE org_id = :org"), params
                )
                await session.execute(
                    text("DELETE FROM ledger.org_sequences WHERE org_id = :org"), params
                )
                await session.execute(
                    text("DELETE FROM ledger.accounts WHERE org_id = :org"), params
                )
                await session.execute(
                    text("DELETE FROM ledger.products WHERE org_id = :org"), params
                )
                await session.execute(
                    text("DELETE FROM ledger.accounting_periods WHERE org_id = :org"),
                    params,
                )
        finally:
            for table in IMMUTABLE_TABLES:
                await session.execute(text(f"ALTER TABLE ledger.{table} ENABLE TRIGGER USER"))
        await session.commit()


@pytest_asyncio.fixture
async def ledger_graph(tracker):
    """Two orgs with their own ledger accounts and posted journal entry.

    Only org A has a receipt (so receipt_lines isolation is observable).
    """
    suffix = uuid.uuid4().hex[:8]
    async with SessionLocal() as session:
        reg = await auth_service.register(
            session,
            RegisterRequest(
                org_name=f"Ledger Org A {suffix}",
                full_name="Ledger Owner A",
                email=f"ledger-a-{suffix}@example.com",
                password="password123",
            ),
        )
    org_a = reg.active_org_id
    tracker.add(str(org_a))

    async with SessionLocal() as session:
        org_b_row = Organization(name=f"Ledger Org B {suffix}", slug=f"ledger-b-{suffix}")
        session.add(org_b_row)
        await session.flush()
        org_b = org_b_row.id
        await session.commit()
    tracker.add(str(org_b))

    accounts_a = [
        {"id": uuid.uuid4(), "code": "1000", "name": "Cash", "kind": "asset"},
        {"id": uuid.uuid4(), "code": "3000", "name": "Equity", "kind": "equity"},
    ]
    accounts_b = [
        {"id": uuid.uuid4(), "code": "1000", "name": "Cash", "kind": "asset"},
        {"id": uuid.uuid4(), "code": "3000", "name": "Equity", "kind": "equity"},
    ]

    async with SessionLocal() as session:
        await _insert_accounts(session, org_a, accounts_a)
        await _insert_accounts(session, org_b, accounts_b)

        await bind_session_context(session, org_id=org_a)
        entry_a = await session.scalar(
            text(
                "SELECT ledger.post_journal_entry("
                "CAST(:org AS uuid), 'test', CAST(:source AS uuid), "
                "CAST(:key AS uuid), now(), :memo, CAST(:lines AS jsonb))"
            ),
            {
                "org": org_a,
                "source": uuid.uuid4(),
                "key": uuid.uuid4(),
                "memo": "org A opening entry",
                "lines": _lines(accounts_a[0]["id"], accounts_a[1]["id"], 100),
            },
        )
        receipt_a = await session.scalar(
            text(
                "SELECT ledger.issue_receipt("
                "CAST(:org AS uuid), CAST(:entry AS uuid), CAST('USD' AS char(3)), "
                "CAST(:lines AS jsonb))"
            ),
            {
                "org": org_a,
                "entry": entry_a,
                "lines": json.dumps(
                    [{"description": "Widget", "quantity": 2, "unit_price": 50}]
                ),
            },
        )

        await bind_session_context(session, org_id=org_b)
        entry_b = await session.scalar(
            text(
                "SELECT ledger.post_journal_entry("
                "CAST(:org AS uuid), 'test', CAST(:source AS uuid), "
                "CAST(:key AS uuid), now(), :memo, CAST(:lines AS jsonb))"
            ),
            {
                "org": org_b,
                "source": uuid.uuid4(),
                "key": uuid.uuid4(),
                "memo": "org B opening entry",
                "lines": _lines(accounts_b[0]["id"], accounts_b[1]["id"], 250),
            },
        )
        await session.commit()

    graph = {
        "org_a": org_a,
        "org_b": org_b,
        "accounts_a": accounts_a,
        "accounts_b": accounts_b,
        "entry_a": entry_a,
        "entry_b": entry_b,
        "receipt_a": receipt_a,
    }
    try:
        yield graph
    finally:
        await _purge_ledger_for_orgs([org_a, org_b])


# ── Structural checks ─────────────────────────────────────


@pytest.mark.parametrize("table_name", LEDGER_TENANT_TABLES)
async def test_ledger_rls_enabled_forced_and_policied(table_name: str) -> None:
    async with SessionLocal() as session:
        row = (
            await session.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE oid = CAST(:t AS regclass)"
                ),
                {"t": f"ledger.{table_name}"},
            )
        ).one_or_none()
        assert row is not None, f"ledger.{table_name} not found"
        assert row[0] is True, f"ledger.{table_name}: RLS not enabled"
        assert row[1] is True, f"ledger.{table_name}: RLS not FORCED"

        policies = await session.scalar(
            text(
                "SELECT count(*) FROM pg_policies "
                "WHERE schemaname = 'ledger' AND tablename = :t"
            ),
            {"t": table_name},
        )
        assert policies and policies >= 1, f"ledger.{table_name}: no RLS policy"


@pytest.mark.parametrize("view_name", LEDGER_VIEWS)
async def test_ledger_views_are_security_invoker(view_name: str) -> None:
    async with SessionLocal() as session:
        reloptions = await session.scalar(
            text(
                "SELECT reloptions FROM pg_class WHERE oid = CAST(:v AS regclass)"
            ),
            {"v": f"ledger.{view_name}"},
        )
    assert reloptions and "security_invoker=true" in reloptions, (
        f"ledger.{view_name} would execute as its owner and bypass RLS"
    )


async def test_app_role_has_read_only_ledger_privileges() -> None:
    async with SessionLocal() as session:
        for privilege in ("INSERT", "UPDATE", "DELETE"):
            allowed = await session.scalar(
                text(
                    "SELECT has_table_privilege('finex_app', 'ledger.accounts', :p)"
                ),
                {"p": privilege},
            )
            assert allowed is False, f"finex_app must not have {privilege} on ledger tables"

        assert await session.scalar(
            text("SELECT has_table_privilege('finex_app', 'ledger.accounts', 'SELECT')")
        )
        assert not await session.scalar(
            text(
                "SELECT has_table_privilege('finex_app', 'ledger.org_sequences', 'SELECT')"
            )
        ), "ledger.org_sequences is internal and must stay unreadable"
        assert await session.scalar(
            text(
                "SELECT has_function_privilege("
                "'finex_app', "
                "'ledger.post_journal_entry(uuid,text,uuid,uuid,timestamptz,text,jsonb,uuid,uuid)', "
                "'EXECUTE')"
            )
        )


# ── Behavioural checks as the restricted app role ─────────


async def test_ledger_tenant_isolation(ledger_graph: dict) -> None:
    org_a = ledger_graph["org_a"]
    org_b = ledger_graph["org_b"]

    async with SessionLocal() as session:
        await session.execute(text(f"SET ROLE {APP_DB_ROLE}"))
        try:
            await bind_session_context(session, org_id=org_a)
            assert await session.scalar(text("SELECT count(*) FROM ledger.accounts")) == 2
            assert (
                await session.scalar(text("SELECT count(*) FROM ledger.journal_entries"))
                == 1
            )
            assert await session.scalar(text("SELECT count(*) FROM ledger.journal_lines")) == 2
            assert await session.scalar(text("SELECT count(*) FROM ledger.v_trial_balance")) == 2
            assert await session.scalar(text("SELECT count(*) FROM ledger.receipts")) == 1
            assert await session.scalar(text("SELECT count(*) FROM ledger.receipt_lines")) == 1

            visible_entries = {
                str(value)
                for value in (
                    await session.scalars(text("SELECT id FROM ledger.journal_entries"))
                ).all()
            }
            assert visible_entries == {str(ledger_graph["entry_a"])}
            visible_receipts = {
                str(value)
                for value in (await session.scalars(text("SELECT id FROM ledger.receipts"))).all()
            }
            assert visible_receipts == {str(ledger_graph["receipt_a"])}

            await bind_session_context(session, org_id=org_b)
            assert await session.scalar(text("SELECT count(*) FROM ledger.accounts")) == 2
            assert (
                await session.scalar(text("SELECT count(*) FROM ledger.journal_entries"))
                == 1
            )
            assert await session.scalar(text("SELECT count(*) FROM ledger.v_trial_balance")) == 2
            # Org B has no receipts; org A's receipt and its lines stay invisible.
            assert await session.scalar(text("SELECT count(*) FROM ledger.receipts")) == 0
            assert await session.scalar(text("SELECT count(*) FROM ledger.receipt_lines")) == 0

            visible_entries = {
                str(value)
                for value in (
                    await session.scalars(text("SELECT id FROM ledger.journal_entries"))
                ).all()
            }
            assert visible_entries == {str(ledger_graph["entry_b"])}

            # No direct table writes, even for the row's own org.
            with pytest.raises(SQLAlchemyError):
                await session.execute(
                    text(
                        "INSERT INTO ledger.accounts (org_id, code, name, account_type) "
                        "VALUES (:org, '9999', 'Probe', 'asset')"
                    ),
                    {"org": org_a},
                )
                await session.flush()
            await session.rollback()
        finally:
            await session.rollback()
            await session.execute(text("RESET ROLE"))


async def test_cross_tenant_posting_is_rejected(ledger_graph: dict) -> None:
    org_a = ledger_graph["org_a"]
    org_b = ledger_graph["org_b"]
    account_a = ledger_graph["accounts_a"][0]["id"]
    account_a2 = ledger_graph["accounts_a"][1]["id"]

    async with SessionLocal() as session:
        await session.execute(text(f"SET ROLE {APP_DB_ROLE}"))
        try:
            # Session is bound to org B, but the function is asked to post to
            # org A.  The SECURITY DEFINER owner could bypass RLS, so the row
            # guard must reject this.
            await bind_session_context(session, org_id=org_b)
            with pytest.raises(SQLAlchemyError) as excinfo:
                await session.execute(
                    text(
                        "SELECT ledger.post_journal_entry("
                        "CAST(:org AS uuid), 'test', CAST(:source AS uuid), "
                        "CAST(:key AS uuid), now(), :memo, CAST(:lines AS jsonb))"
                    ),
                    {
                        "org": org_a,
                        "source": uuid.uuid4(),
                        "key": uuid.uuid4(),
                        "memo": "cross tenant attempt",
                        "lines": _lines(account_a, account_a2, 100),
                    },
                )
                await session.flush()
            assert "organisation context mismatch" in str(excinfo.value)
            await session.rollback()
        finally:
            await session.rollback()
            await session.execute(text("RESET ROLE"))

    # Nothing was written for org A beyond the fixture's original entry.
    async with SessionLocal() as session:
        count = await session.scalar(
            text("SELECT count(*) FROM ledger.posting_events WHERE org_id = :org"),
            {"org": org_a},
        )
    assert count == 1


async def test_posting_is_idempotent(ledger_graph: dict) -> None:
    org_a = ledger_graph["org_a"]
    accounts_a = ledger_graph["accounts_a"]
    idempotency_key = uuid.uuid4()
    source_id = uuid.uuid4()
    params = {
        "org": org_a,
        "source": source_id,
        "key": idempotency_key,
        "memo": "idempotency probe",
        "lines": _lines(accounts_a[0]["id"], accounts_a[1]["id"], 42),
    }
    statement = text(
        "SELECT ledger.post_journal_entry("
        "CAST(:org AS uuid), 'test', CAST(:source AS uuid), "
        "CAST(:key AS uuid), now(), :memo, CAST(:lines AS jsonb))"
    )

    async with SessionLocal() as session:
        await bind_session_context(session, org_id=org_a)
        first = await session.scalar(statement, params)
        second = await session.scalar(statement, params)
        await session.commit()

    assert first == second, "the same idempotency key must return the original entry"

    async with SessionLocal() as session:
        count = await session.scalar(
            text("SELECT count(*) FROM ledger.journal_entries WHERE org_id = :org"),
            {"org": org_a},
        )
    assert count == 2, "idempotent replay must not create a second journal entry"
