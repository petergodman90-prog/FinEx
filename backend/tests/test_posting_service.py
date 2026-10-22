"""Person A posting-service acceptance tests.

These tests exercise the application service, not just the SQL functions:

* default chart of accounts is idempotent;
* product/account setup and opening stock work;
* a sale posts exactly one balanced journal entry, one stock movement per
  product, sale lines, and (optionally) a receipt in one transaction;
* duplicate and concurrent approvals return the same journal entry;
* an insufficient-stock or closed-period failure leaves no partial writes;
* cross-organisation customers/products/accounts are rejected;
* reversal restores stock and is idempotent;
* manual proposals are not postable.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.db import SessionLocal
from app.core.rls import bind_session_context
from app.schemas.auth import RegisterRequest
from app.services import auth_service, posting_service
from app.services.posting_service import (
    PostingRetryableError,
    PostingValidationError,
)

IMMUTABLE_TABLES = [
    "journal_entries",
    "journal_lines",
    "stock_movements",
    "posting_events",
    "receipts",
    "receipt_lines",
    "open_items",
    "payment_allocations",
    "sale_lines",
]


async def _purge_ledger(org_ids: list[uuid.UUID]) -> None:
    async with SessionLocal() as session:
        for table in IMMUTABLE_TABLES:
            await session.execute(
                text(f"ALTER TABLE ledger.{table} DISABLE TRIGGER USER")
            )
        try:
            for org_id in org_ids:
                params = {"org": org_id}
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
                for table in (
                    "receipts",
                    "open_items",
                    "sale_lines",
                    "stock_movements",
                    "journal_lines",
                    "journal_entries",
                    "posting_events",
                    "org_sequences",
                    "accounts",
                    "products",
                    "suppliers",
                    "accounting_periods",
                ):
                    await session.execute(
                        text(f"DELETE FROM ledger.{table} WHERE org_id = :org"),
                        params,
                    )
        finally:
            for table in IMMUTABLE_TABLES:
                await session.execute(
                    text(f"ALTER TABLE ledger.{table} ENABLE TRIGGER USER")
                )
        await session.commit()


async def _register_org(tracker, label: str) -> dict:
    suffix = uuid.uuid4().hex[:10]
    async with SessionLocal() as session:
        registration = await auth_service.register(
            session,
            RegisterRequest(
                org_name=f"{label} {suffix}",
                full_name=f"{label} Owner",
                email=f"{label.lower()}-{suffix}@example.com",
                password="password123",
            ),
        )
        await session.commit()
    org_id = registration.active_org_id
    user_id = registration.user.id
    tracker.add(str(org_id))
    return {"org_id": org_id, "user_id": user_id}


@pytest_asyncio.fixture
async def org_factory(tracker):
    created: list[uuid.UUID] = []

    async def _make(label: str = "Posting") -> dict:
        org = await _register_org(tracker, label)
        created.append(org["org_id"])
        return org

    try:
        yield _make
    finally:
        if created:
            await _purge_ledger(created)


async def _bind(session, org: dict) -> None:
    await bind_session_context(
        session, org_id=org["org_id"], user_id=org["user_id"], role="owner"
    )


async def _create_customer(org: dict) -> uuid.UUID:
    customer_id = uuid.uuid4()
    async with SessionLocal() as session:
        async with session.begin():
            await _bind(session, org)
            await session.execute(
                text(
                    """
                    INSERT INTO public.customers (id, org_id, name, tags, source)
                    VALUES (CAST(:id AS uuid), CAST(:org AS uuid), :name,
                            CAST('[]' AS jsonb), 'manual')
                    """
                ),
                {"id": customer_id, "org": org["org_id"], "name": "Ada Customer"},
            )
    return customer_id


async def _create_product(org: dict, *, sku: str = "SKU-1", cost: str = "10.00") -> uuid.UUID:
    async with SessionLocal() as session:
        async with session.begin():
            product_id = await posting_service.create_product(
                session,
                org["org_id"],
                sku=sku,
                name=f"Product {sku}",
                sale_price="20.00",
                cost=cost,
                reorder_point="1",
                user_id=org["user_id"],
            )
    return product_id


async def _add_opening_stock(org: dict, product_id: uuid.UUID, quantity: str = "10") -> None:
    async with SessionLocal() as session:
        async with session.begin():
            await posting_service.post_opening_stock(
                session,
                org_id=org["org_id"],
                idempotency_key=uuid.uuid4(),
                payload={
                    "effective_at": "2026-01-01T09:00:00Z",
                    "lines": [
                        {
                            "product_id": str(product_id),
                            "quantity": quantity,
                            "unit_cost": "10.00",
                        }
                    ],
                },
                requested_by=org["user_id"],
            )


def _sale_payload(
    customer_id: uuid.UUID,
    product_id: uuid.UUID,
    *,
    quantity: str = "2",
    unit_price: str = "20.00",
    unit_cost: str = "10.00",
    method: str = "cash",
    effective_at: str = "2026-01-15T10:30:00Z",
) -> dict:
    return {
        "customer_id": str(customer_id),
        "currency": "NGN",
        "effective_at": effective_at,
        "lines": [
            {
                "product_id": str(product_id),
                "quantity": quantity,
                "unit_price": unit_price,
                "unit_cost": unit_cost,
            }
        ],
        "payment": {"method": method},
    }


async def _post_sale(org: dict, proposal_id: uuid.UUID, payload: dict):
    async with SessionLocal() as session:
        async with session.begin():
            await _bind(session, org)
            return await posting_service.post_proposal(
                session,
                org_id=org["org_id"],
                proposal_id=proposal_id,
                proposal_type="sale",
                payload=payload,
                requested_by=org["user_id"],
                role="owner",
            )


async def _counts(org: dict) -> dict:
    async with SessionLocal() as session:
        async with session.begin():
            await _bind(session, org)
            result = {}
            for table in (
                "journal_entries",
                "journal_lines",
                "stock_movements",
                "receipts",
                "sale_lines",
                "posting_events",
                "open_items",
            ):
                result[table] = await session.scalar(
                    text(f"SELECT count(*) FROM ledger.{table} WHERE org_id = :org"),
                    {"org": org["org_id"]},
                )
            result["stock"] = await session.scalar(
                text(
                    "SELECT coalesce(sum(quantity_delta), 0) FROM ledger.stock_movements "
                    "WHERE org_id = :org"
                ),
                {"org": org["org_id"]},
            )
            return result


# ── Setup ────────────────────────────────────────────────────


async def test_default_chart_is_idempotent_and_setup_operations_work(org_factory) -> None:
    org = await org_factory("Chart")

    async with SessionLocal() as session:
        async with session.begin():
            await _bind(session, org)
            first = await posting_service.ensure_default_chart(session, org["org_id"])
            second = await posting_service.ensure_default_chart(session, org["org_id"])

    assert first.keys() == second.keys()
    assert {"1000", "1100", "1200", "2000", "3000", "4000", "5000", "6000"} <= set(first)
    assert len(second) == 19

    async with SessionLocal() as session:
        async with session.begin():
            account_id = await posting_service.create_account(
                session,
                org["org_id"],
                code="7000",
                name="Travel",
                account_type="expense",
            )
            await posting_service.update_account(
                session, org["org_id"], account_id, name="Travel & Subsistence"
            )
            await posting_service.update_product(
                session,
                org["org_id"],
                await _create_product(org),
                cost="12.50",
            )

    async with SessionLocal() as session:
        async with session.begin():
            await _bind(session, org)
            row = (
                await session.execute(
                    text(
                        "SELECT name, account_type FROM ledger.accounts "
                        "WHERE id = CAST(:id AS uuid)"
                    ),
                    {"id": account_id},
                )
            ).one()
    assert row.name == "Travel & Subsistence"
    assert row.account_type == "expense"


# ── Sale posting ─────────────────────────────────────────────


async def test_sale_posts_one_balanced_entry_stock_and_receipt(org_factory) -> None:
    org = await org_factory("Sale")
    product_id = await _create_product(org)
    customer_id = await _create_customer(org)
    await _add_opening_stock(org, product_id, "10")
    before = await _counts(org)

    proposal_id = uuid.uuid4()
    result = await _post_sale(
        org, proposal_id, _sale_payload(customer_id, product_id)
    )

    assert result.replayed is False
    assert result.receipt_id is not None
    assert len(result.stock_movement_ids) == 1

    after = await _counts(org)
    assert after["journal_entries"] == before["journal_entries"] + 1
    assert after["journal_lines"] == before["journal_lines"] + 4
    assert after["stock_movements"] == before["stock_movements"] + 1
    assert after["sale_lines"] == before["sale_lines"] + 1
    assert after["receipts"] == before["receipts"] + 1
    assert after["posting_events"] == before["posting_events"] + 1
    assert Decimal(after["stock"]) == Decimal(before["stock"]) - Decimal("2")

    async with SessionLocal() as session:
        async with session.begin():
            await _bind(session, org)
            totals = (
                await session.execute(
                    text(
                        """
                        SELECT sum(debit) AS debit, sum(credit) AS credit
                        FROM ledger.journal_lines
                        WHERE journal_entry_id = CAST(:entry AS uuid)
                        """
                    ),
                    {"entry": result.journal_entry_id},
                )
            ).one()
            revenue_line = await session.scalar(
                text(
                    """
                    SELECT sum(credit) FROM ledger.journal_lines jl
                    JOIN ledger.accounts a ON a.id = jl.account_id
                    WHERE jl.journal_entry_id = CAST(:entry AS uuid)
                      AND a.code = '4000'
                    """
                ),
                {"entry": result.journal_entry_id},
            )
            cogs_line = await session.scalar(
                text(
                    """
                    SELECT sum(debit) FROM ledger.journal_lines jl
                    JOIN ledger.accounts a ON a.id = jl.account_id
                    WHERE jl.journal_entry_id = CAST(:entry AS uuid)
                      AND a.code = '5000'
                    """
                ),
                {"entry": result.journal_entry_id},
            )
    assert Decimal(totals.debit) == Decimal(totals.credit) == Decimal("60.0000")
    assert Decimal(revenue_line) == Decimal("40.0000")
    assert Decimal(cogs_line) == Decimal("20.0000")

    # A duplicate approval returns the original entry and writes nothing new.
    replay = await _post_sale(org, proposal_id, _sale_payload(customer_id, product_id))
    assert replay.replayed is True
    assert replay.journal_entry_id == result.journal_entry_id
    assert await _counts(org) == after


async def test_receivable_sale_creates_open_item_without_receipt(org_factory) -> None:
    org = await org_factory("Receivable")
    product_id = await _create_product(org)
    customer_id = await _create_customer(org)
    await _add_opening_stock(org, product_id)

    result = await _post_sale(
        org,
        uuid.uuid4(),
        _sale_payload(customer_id, product_id, method="receivable"),
    )
    assert result.receipt_id is None
    assert result.open_item_id is not None

    async with SessionLocal() as session:
        async with session.begin():
            await _bind(session, org)
            row = (
                await session.execute(
                    text(
                        "SELECT item_type, original_amount FROM ledger.open_items "
                        "WHERE id = CAST(:id AS uuid)"
                    ),
                    {"id": result.open_item_id},
                )
            ).one()
    assert row.item_type == "customer_receivable"
    assert Decimal(row.original_amount) == Decimal("40.0000")


async def test_expense_cash_and_payable(org_factory) -> None:
    org = await org_factory("Expense")
    await _create_customer(org)

    async with SessionLocal() as session:
        async with session.begin():
            await posting_service.post_proposal(
                session,
                org_id=org["org_id"],
                proposal_id=uuid.uuid4(),
                proposal_type="expense",
                payload={
                    "amount": "75.50",
                    "currency": "NGN",
                    "effective_at": "2026-01-20T12:00:00Z",
                    "category": "rent",
                    "vendor": "Landlord",
                },
                requested_by=org["user_id"],
            )

    supplier_id = None
    async with SessionLocal() as session:
        async with session.begin():
            supplier_id = await posting_service.create_supplier(
                session, org["org_id"], name="Paper Co"
            )

    async with SessionLocal() as session:
        async with session.begin():
            result = await posting_service.post_proposal(
                session,
                org_id=org["org_id"],
                proposal_id=uuid.uuid4(),
                proposal_type="expense",
                payload={
                    "amount": "25.00",
                    "currency": "NGN",
                    "effective_at": "2026-01-21T12:00:00Z",
                    "expense_account_code": "6000",
                    "vendor": "Paper Co",
                    "payment": {
                        "method": "payable",
                        "supplier_id": str(supplier_id),
                        "due_on": "2026-02-20",
                    },
                },
                requested_by=org["user_id"],
            )
    assert result.open_item_id is not None


# ── Failures and isolation ───────────────────────────────────


async def test_insufficient_stock_rolls_back_all_financial_writes(org_factory) -> None:
    org = await org_factory("Stock")
    product_id = await _create_product(org)
    customer_id = await _create_customer(org)
    await _add_opening_stock(org, product_id, "1")
    before = await _counts(org)

    with pytest.raises(PostingRetryableError) as excinfo:
        await _post_sale(
            org,
            uuid.uuid4(),
            _sale_payload(customer_id, product_id, quantity="5"),
        )
    assert excinfo.value.code == "insufficient_stock"
    assert excinfo.value.retryable is True
    assert await _counts(org) == before


async def test_closed_period_rejected_without_partial_writes(org_factory) -> None:
    org = await org_factory("Closed")
    product_id = await _create_product(org)
    customer_id = await _create_customer(org)
    await _add_opening_stock(org, product_id)
    before = await _counts(org)

    async with SessionLocal() as session:
        async with session.begin():
            await _bind(session, org)
            await session.execute(
                text(
                    "SELECT ledger.create_accounting_period("
                    "CAST(:org AS uuid), CAST('2026-01-01' AS date), CAST('2026-01-31' AS date))"
                ),
                {"org": org["org_id"]},
            )
            await session.execute(
                text(
                    "SELECT ledger.set_accounting_period_state("
                    "CAST(:org AS uuid), CAST('2026-01-01' AS date), "
                    "CAST('2026-01-31' AS date), true)"
                ),
                {"org": org["org_id"]},
            )

    with pytest.raises(PostingRetryableError) as excinfo:
        await _post_sale(
            org,
            uuid.uuid4(),
            _sale_payload(
                customer_id, product_id, effective_at="2026-01-15T10:30:00Z"
            ),
        )
    assert excinfo.value.code == "closed_period"
    assert await _counts(org) == before


async def test_cross_organisation_customer_and_account_rejected(org_factory) -> None:
    org_a = await org_factory("OrgA")
    org_b = await org_factory("OrgB")
    product_a = await _create_product(org_a, sku="A-1")
    customer_a = await _create_customer(org_a)
    await _add_opening_stock(org_a, product_a)

    with pytest.raises(PostingValidationError) as excinfo:
        await _post_sale(
            org_b,
            uuid.uuid4(),
            _sale_payload(customer_a, product_a),
        )
    assert excinfo.value.code == "customer_not_found"

    # An account that exists only in org A is not resolvable in org B.
    async with SessionLocal() as session:
        async with session.begin():
            await posting_service.create_account(
                session,
                org_a["org_id"],
                code="7999",
                name="Org A only",
                account_type="expense",
            )

    async with SessionLocal() as session:
        async with session.begin():
            with pytest.raises(PostingValidationError) as excinfo:
                await posting_service.post_proposal(
                    session,
                    org_id=org_b["org_id"],
                    proposal_id=uuid.uuid4(),
                    proposal_type="expense",
                    payload={
                        "amount": "5",
                        "currency": "NGN",
                        "effective_at": "2026-02-01T00:00:00Z",
                        "expense_account_code": "7999",
                    },
                )
    assert excinfo.value.code == "account_not_found"


async def test_unbalanced_journal_is_rejected(org_factory) -> None:
    org = await org_factory("Unbalanced")

    async with SessionLocal() as session:
        async with session.begin():
            await _bind(session, org)
            chart = await posting_service.ensure_default_chart(session, org["org_id"])
            with pytest.raises(DBAPIError) as excinfo:
                await session.execute(
                    text(
                        """
                        SELECT ledger.post_journal_entry(
                            CAST(:org AS uuid), 'test', CAST(:source AS uuid),
                            CAST(:key AS uuid), now(), 'bad',
                            CAST(:lines AS jsonb)
                        )
                        """
                    ),
                    {
                        "org": org["org_id"],
                        "source": uuid.uuid4(),
                        "key": uuid.uuid4(),
                        "lines": json.dumps(
                            [
                                {
                                    "account_id": str(chart["1000"].id),
                                    "debit": 10,
                                    "credit": 0,
                                },
                                {
                                    "account_id": str(chart["3000"].id),
                                    "debit": 0,
                                    "credit": 5,
                                },
                            ]
                        ),
                    },
                )
    assert "unbalanced" in str(excinfo.value)


async def test_manual_proposal_is_not_postable(org_factory) -> None:
    org = await org_factory("Manual")
    async with SessionLocal() as session:
        async with session.begin():
            with pytest.raises(PostingValidationError) as excinfo:
                await posting_service.post_proposal(
                    session,
                    org_id=org["org_id"],
                    proposal_id=uuid.uuid4(),
                    proposal_type="manual",
                    payload={"note": "just a memo"},
                )
    assert excinfo.value.code == "manual_not_postable"


# ── Concurrency and reversal ─────────────────────────────────


async def test_concurrent_approval_posts_exactly_once(org_factory) -> None:
    org = await org_factory("Concurrent")
    product_id = await _create_product(org)
    customer_id = await _create_customer(org)
    await _add_opening_stock(org, product_id)
    before = await _counts(org)
    proposal_id = uuid.uuid4()
    payload = _sale_payload(customer_id, product_id)

    async def attempt():
        async with SessionLocal() as session:
            async with session.begin():
                await _bind(session, org)
                return await posting_service.post_proposal(
                    session,
                    org_id=org["org_id"],
                    proposal_id=proposal_id,
                    proposal_type="sale",
                    payload=payload,
                    requested_by=org["user_id"],
                )

    first, second = await asyncio.gather(attempt(), attempt())

    assert first.journal_entry_id == second.journal_entry_id
    assert {first.replayed, second.replayed} == {False, True}

    after = await _counts(org)
    assert after["journal_entries"] == before["journal_entries"] + 1
    assert after["posting_events"] == before["posting_events"] + 1
    assert after["stock_movements"] == before["stock_movements"] + 1
    assert after["receipts"] == before["receipts"] + 1


async def test_reversal_restores_stock_and_is_idempotent(org_factory) -> None:
    org = await org_factory("Reversal")
    product_id = await _create_product(org)
    customer_id = await _create_customer(org)
    await _add_opening_stock(org, product_id)
    before = await _counts(org)

    sale = await _post_sale(
        org, uuid.uuid4(), _sale_payload(customer_id, product_id)
    )

    async with SessionLocal() as session:
        async with session.begin():
            await _bind(session, org)
            reversal = await posting_service.reverse_posting(
                session,
                org_id=org["org_id"],
                journal_entry_id=sale.journal_entry_id,
                effective_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
                requested_by=org["user_id"],
            )
    assert reversal.journal_entry_id != sale.journal_entry_id

    after = await _counts(org)
    assert after["journal_entries"] == before["journal_entries"] + 2
    assert Decimal(after["stock"]) == Decimal(before["stock"])

    async with SessionLocal() as session:
        async with session.begin():
            await _bind(session, org)
            replay = await posting_service.reverse_posting(
                session,
                org_id=org["org_id"],
                journal_entry_id=sale.journal_entry_id,
                effective_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
                requested_by=org["user_id"],
            )
    assert replay.replayed is True
    assert replay.journal_entry_id == reversal.journal_entry_id
    assert await _counts(org) == after
