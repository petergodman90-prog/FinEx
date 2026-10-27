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
from app.services import auth_service, ledger_service, posting_service
from app.services.posting_service import (
    PostingConflictError,
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


def _credit_payload(
    product_id: uuid.UUID,
    *,
    customer_id: uuid.UUID | None = None,
    currency: str | None = "NGN",
    quantity: str = "1",
    unit_price: str = "20.00",
    unit_cost: str = "10.00",
    original_receipt_id: uuid.UUID | None = None,
    restock: bool = True,
    effective_at: str = "2026-01-18T10:00:00Z",
) -> dict:
    payload = {
        "effective_at": effective_at,
        "lines": [
            {
                "product_id": str(product_id),
                "quantity": quantity,
                "unit_price": unit_price,
                "unit_cost": unit_cost,
            }
        ],
        "restock": restock,
    }
    if customer_id is not None:
        payload["customer_id"] = str(customer_id)
    if currency is not None:
        payload["currency"] = currency
    if original_receipt_id is not None:
        payload["original_receipt_id"] = str(original_receipt_id)
    return payload


async def _post_credit_note(org: dict, proposal_id: uuid.UUID, payload: dict):
    async with SessionLocal() as session:
        async with session.begin():
            await _bind(session, org)
            return await posting_service.post_proposal(
                session,
                org_id=org["org_id"],
                proposal_id=proposal_id,
                proposal_type="credit_note",
                payload=payload,
                requested_by=org["user_id"],
                role="owner",
            )


async def _create_supplier(org: dict, name: str = "Report Supplier") -> uuid.UUID:
    async with SessionLocal() as session:
        async with session.begin():
            return await posting_service.create_supplier(
                session, org["org_id"], name=name
            )


async def _post_expense(
    org: dict,
    *,
    amount: str = "10.00",
    category: str = "rent",
    method: str = "cash",
    supplier_id: uuid.UUID | None = None,
    effective_at: str = "2026-01-20T12:00:00Z",
):
    payment = {"method": method}
    if method == "payable":
        payment["supplier_id"] = str(supplier_id)
    async with SessionLocal() as session:
        async with session.begin():
            return await posting_service.post_proposal(
                session,
                org_id=org["org_id"],
                proposal_id=uuid.uuid4(),
                proposal_type="expense",
                payload={
                    "amount": amount,
                    "currency": "NGN",
                    "effective_at": effective_at,
                    "category": category,
                    "payment": payment,
                },
                requested_by=org["user_id"],
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


async def test_credit_note_refunds_cash_and_returns_stock(org_factory) -> None:
    org = await org_factory("CreditNote")
    product_id = await _create_product(org)
    customer_id = await _create_customer(org)
    await _add_opening_stock(org, product_id, "10")
    sale = await _post_sale(
        org, uuid.uuid4(), _sale_payload(customer_id, product_id, quantity="2")
    )
    assert sale.receipt_id is not None
    before = await _counts(org)

    proposal_id = uuid.uuid4()
    credit = await _post_credit_note(
        org,
        proposal_id,
        _credit_payload(
            product_id,
            customer_id=None,
            currency=None,
            original_receipt_id=sale.receipt_id,
        ),
    )

    assert credit.replayed is False
    assert credit.credit_note_id is not None
    assert credit.receipt_id is None
    assert len(credit.stock_movement_ids) == 1

    after = await _counts(org)
    assert after["journal_entries"] == before["journal_entries"] + 1
    assert after["journal_lines"] == before["journal_lines"] + 4
    assert after["stock_movements"] == before["stock_movements"] + 1
    assert after["receipts"] == before["receipts"] + 1
    assert after["sale_lines"] == before["sale_lines"]
    assert after["posting_events"] == before["posting_events"] + 1
    assert Decimal(after["stock"]) == Decimal(before["stock"]) + Decimal("1")

    async with SessionLocal() as session:
        async with session.begin():
            await _bind(session, org)
            lines = await ledger_service.get_journal_lines(
                session, org["org_id"], credit.journal_entry_id
            )
            by_code = {line.account_code: line for line in lines}
            document = (
                await session.execute(
                    text(
                        """
                        SELECT receipt_type, total_amount, credit_note_of_id,
                               customer_id, currency_code
                        FROM ledger.receipts
                        WHERE id = CAST(:id AS uuid)
                        """
                    ),
                    {"id": credit.credit_note_id},
                )
            ).one()
            movement = (
                await session.execute(
                    text(
                        """
                        SELECT movement_type, quantity_delta, unit_cost
                        FROM ledger.stock_movements
                        WHERE id = CAST(:id AS uuid)
                        """
                    ),
                    {"id": credit.stock_movement_ids[0]},
                )
            ).one()
            stock = await ledger_service.stock_valuation(session, org["org_id"])

    assert by_code["4000"].debit == Decimal("20")
    assert by_code["4000"].credit == Decimal("0")
    assert by_code["1000"].credit == Decimal("20")
    assert by_code["1200"].debit == Decimal("10")
    assert by_code["5000"].credit == Decimal("10")
    assert sum(line.debit for line in lines) == sum(line.credit for line in lines) == Decimal(
        "30"
    )
    assert document.receipt_type == "credit_note"
    assert Decimal(document.total_amount) == Decimal("20.0000")
    assert document.credit_note_of_id == sale.receipt_id
    assert document.customer_id == customer_id
    assert document.currency_code == "NGN"
    assert movement.movement_type == "return_in"
    assert Decimal(movement.quantity_delta) == Decimal("1.0000")
    assert Decimal(movement.unit_cost) == Decimal("10.0000")
    assert stock.rows[0].quantity_on_hand == Decimal("9")
    assert stock.rows[0].book_value == Decimal("90")

    replay = await _post_credit_note(
        org,
        proposal_id,
        _credit_payload(
            product_id,
            customer_id=None,
            currency=None,
            original_receipt_id=sale.receipt_id,
        ),
    )
    assert replay.replayed is True
    assert replay.journal_entry_id == credit.journal_entry_id
    assert replay.credit_note_id == credit.credit_note_id
    assert await _counts(org) == after


async def test_credit_note_without_restock_is_capped_by_receipt(org_factory) -> None:
    org = await org_factory("CreditCap")
    product_id = await _create_product(org)
    customer_id = await _create_customer(org)
    await _add_opening_stock(org, product_id, "10")
    sale = await _post_sale(
        org, uuid.uuid4(), _sale_payload(customer_id, product_id, quantity="2")
    )
    assert sale.receipt_id is not None
    before = await _counts(org)

    first = await _post_credit_note(
        org,
        uuid.uuid4(),
        _credit_payload(
            product_id,
            customer_id=customer_id,
            original_receipt_id=sale.receipt_id,
            restock=False,
        ),
    )
    assert first.credit_note_id is not None
    after_first = await _counts(org)
    assert after_first["stock_movements"] == before["stock_movements"]
    assert after_first["stock"] == before["stock"]
    assert after_first["journal_lines"] == before["journal_lines"] + 2

    async with SessionLocal() as session:
        async with session.begin():
            await _bind(session, org)
            lines = await ledger_service.get_journal_lines(
                session, org["org_id"], first.journal_entry_id
            )
    assert {line.account_code for line in lines} == {"1000", "4000"}

    second = await _post_credit_note(
        org,
        uuid.uuid4(),
        _credit_payload(
            product_id,
            customer_id=customer_id,
            original_receipt_id=sale.receipt_id,
            restock=False,
        ),
    )
    assert second.credit_note_id is not None

    before_rejection = await _counts(org)
    with pytest.raises(PostingValidationError) as excinfo:
        await _post_credit_note(
            org,
            uuid.uuid4(),
            _credit_payload(
                product_id,
                customer_id=customer_id,
                original_receipt_id=sale.receipt_id,
                quantity="0.01",
                unit_price="20.00",
                restock=False,
            ),
        )
    assert excinfo.value.code == "invalid_credit_note_amount"
    assert await _counts(org) == before_rejection


async def test_credit_note_source_and_reference_guards(org_factory) -> None:
    org_a = await org_factory("CreditSourceA")
    org_b = await org_factory("CreditSourceB")
    product_id = await _create_product(org_a)
    customer_id = await _create_customer(org_a)
    other_customer_id = await _create_customer(org_a)
    await _add_opening_stock(org_a, product_id, "10")
    sale = await _post_sale(
        org_a, uuid.uuid4(), _sale_payload(customer_id, product_id)
    )
    assert sale.receipt_id is not None

    with pytest.raises(PostingValidationError) as excinfo:
        await _post_credit_note(
            org_b,
            uuid.uuid4(),
            _credit_payload(
                product_id,
                customer_id=None,
                currency=None,
                original_receipt_id=sale.receipt_id,
            ),
        )
    assert excinfo.value.code == "receipt_not_found"

    credit = await _post_credit_note(
        org_a,
        uuid.uuid4(),
        _credit_payload(
            product_id,
            customer_id=None,
            currency=None,
            original_receipt_id=sale.receipt_id,
        ),
    )
    assert credit.credit_note_id is not None
    before = await _counts(org_a)

    with pytest.raises(PostingValidationError) as excinfo:
        await _post_credit_note(
            org_a,
            uuid.uuid4(),
            _credit_payload(
                product_id,
                customer_id=None,
                currency=None,
                original_receipt_id=credit.credit_note_id,
            ),
        )
    assert excinfo.value.code == "invalid_credit_note_source"

    with pytest.raises(PostingValidationError) as excinfo:
        await _post_credit_note(
            org_a,
            uuid.uuid4(),
            _credit_payload(
                product_id,
                customer_id=other_customer_id,
                original_receipt_id=sale.receipt_id,
            ),
        )
    assert excinfo.value.code == "customer_mismatch"

    with pytest.raises(PostingValidationError) as excinfo:
        await _post_credit_note(
            org_a,
            uuid.uuid4(),
            _credit_payload(
                product_id,
                customer_id=customer_id,
                currency="USD",
                original_receipt_id=sale.receipt_id,
            ),
        )
    assert excinfo.value.code == "currency_mismatch"
    assert await _counts(org_a) == before


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
            await posting_service.create_accounting_period(
                session,
                org["org_id"],
                start=date(2026, 1, 1),
                end=date(2026, 1, 31),
            )
            await posting_service.close_accounting_period(
                session,
                org["org_id"],
                start=date(2026, 1, 1),
                end=date(2026, 1, 31),
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


async def test_accounting_period_state_is_validated_and_reopenable(org_factory) -> None:
    org = await org_factory("PeriodState")

    with pytest.raises(PostingValidationError) as excinfo:
        async with SessionLocal() as session:
            async with session.begin():
                await posting_service.create_accounting_period(
                    session,
                    org["org_id"],
                    start=date(2026, 2, 1),
                    end=date(2026, 1, 1),
                )
    assert excinfo.value.code == "invalid_period"

    async with SessionLocal() as session:
        async with session.begin():
            period_id = await posting_service.create_accounting_period(
                session,
                org["org_id"],
                start=date(2026, 1, 1),
                end=date(2026, 1, 31),
            )
            with pytest.raises(PostingConflictError) as excinfo:
                await posting_service.create_accounting_period(
                    session,
                    org["org_id"],
                    start=date(2026, 1, 1),
                    end=date(2026, 1, 31),
                )
    assert excinfo.value.code == "duplicate_posting"

    async with SessionLocal() as session:
        async with session.begin():
            first_close = await posting_service.close_accounting_period(
                session,
                org["org_id"],
                start=date(2026, 1, 1),
                end=date(2026, 1, 31),
            )
            second_close = await posting_service.close_accounting_period(
                session,
                org["org_id"],
                start=date(2026, 1, 1),
                end=date(2026, 1, 31),
            )
            await posting_service.reopen_accounting_period(
                session,
                org["org_id"],
                start=date(2026, 1, 1),
                end=date(2026, 1, 31),
            )
            periods = await ledger_service.list_accounting_periods(
                session, org["org_id"]
            )
            with pytest.raises(PostingValidationError) as excinfo:
                await posting_service.set_accounting_period_state(
                    session,
                    org["org_id"],
                    start=date(2026, 2, 1),
                    end=date(2026, 2, 28),
                    closed=True,
                )
    assert first_close == second_close == period_id
    assert periods[0]["is_closed"] is False
    assert excinfo.value.code == "accounting_period_not_found"


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


# ── Reports ──────────────────────────────────────────────────


async def test_reports_profit_loss_balance_sheet_and_stock_valuation(
    org_factory,
) -> None:
    org = await org_factory("Reports")
    product_id = await _create_product(org)
    customer_id = await _create_customer(org)
    await _add_opening_stock(org, product_id, "10")

    sale = await _post_sale(
        org, uuid.uuid4(), _sale_payload(customer_id, product_id)
    )
    assert sale.receipt_id is not None
    await _post_expense(org, amount="10.00", category="rent")

    async with SessionLocal() as session:
        async with session.begin():
            await _bind(session, org)
            pnl = await ledger_service.profit_and_loss(
                session,
                org["org_id"],
                start=date(2026, 1, 1),
                end=date(2026, 1, 31),
            )
            balance = await ledger_service.balance_sheet(
                session, org["org_id"], as_of=date(2026, 1, 31)
            )
            stock = await ledger_service.stock_valuation(session, org["org_id"])
            trial = await ledger_service.trial_balance(session, org["org_id"])

    assert pnl.revenue_total == Decimal("40")
    assert pnl.cogs_total == Decimal("20")
    assert pnl.gross_profit == Decimal("20")
    assert pnl.operating_expense_total == Decimal("10")
    assert pnl.net_profit == Decimal("10")
    assert {line.account_code: line.amount for line in pnl.revenue_lines} == {
        "4000": Decimal("40")
    }
    assert {line.account_code: line.amount for line in pnl.cogs_lines} == {
        "5000": Decimal("20")
    }
    assert {
        line.account_code: line.amount for line in pnl.operating_expense_lines
    } == {"6100": Decimal("10")}

    assert balance.total_assets == Decimal("110")
    assert balance.total_liabilities == Decimal("0")
    assert balance.total_equity == Decimal("100")
    assert balance.current_earnings == Decimal("10")
    assert balance.total_liabilities_and_equity == Decimal("110")
    assert balance.is_balanced is True
    assert {
        line.account_code: line.amount for line in balance.asset_lines
    } == {"1000": Decimal("30"), "1200": Decimal("80")}
    assert {line.account_code: line.amount for line in balance.equity_lines} == {
        "3000": Decimal("100")
    }

    assert len(stock.rows) == 1
    row = stock.rows[0]
    assert row.product_id == product_id
    assert row.quantity_on_hand == Decimal("8")
    assert row.book_value == Decimal("80")
    assert row.average_unit_cost == Decimal("10")
    assert stock.total_book_value == Decimal("80")
    assert stock.inventory_account_balance == Decimal("80")
    assert stock.is_reconciled is True

    assert sum((line.debit_total for line in trial), Decimal("0")) == sum(
        (line.credit_total for line in trial), Decimal("0")
    )
    assert sum((line.net_debit_balance for line in trial), Decimal("0")) == Decimal(
        "0"
    )


async def test_reports_respect_period_and_reversal(org_factory) -> None:
    org = await org_factory("ReportReversal")
    product_id = await _create_product(org)
    customer_id = await _create_customer(org)
    await _add_opening_stock(org, product_id, "10")

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

    async with SessionLocal() as session:
        async with session.begin():
            await _bind(session, org)
            january = await ledger_service.profit_and_loss(
                session,
                org["org_id"],
                start=date(2026, 1, 1),
                end=date(2026, 1, 31),
            )
            february = await ledger_service.profit_and_loss(
                session,
                org["org_id"],
                start=date(2026, 2, 1),
                end=date(2026, 2, 28),
            )
            combined = await ledger_service.profit_and_loss(
                session,
                org["org_id"],
                start=date(2026, 1, 1),
                end=date(2026, 2, 28),
            )
            balance = await ledger_service.balance_sheet(
                session, org["org_id"], as_of=date(2026, 2, 1)
            )
            stock = await ledger_service.stock_valuation(session, org["org_id"])

    assert reversal.journal_entry_id != sale.journal_entry_id
    assert january.revenue_total == Decimal("40")
    assert january.cogs_total == Decimal("20")
    assert january.net_profit == Decimal("20")
    assert february.revenue_total == Decimal("-40")
    assert february.cogs_total == Decimal("-20")
    assert february.net_profit == Decimal("-20")
    assert combined.revenue_total == Decimal("0")
    assert combined.cogs_total == Decimal("0")
    assert combined.net_profit == Decimal("0")

    assert balance.total_assets == Decimal("100")
    assert balance.total_equity == Decimal("100")
    assert balance.current_earnings == Decimal("0")
    assert balance.is_balanced is True
    assert stock.total_book_value == Decimal("100")
    assert stock.is_reconciled is True
    assert stock.rows[0].quantity_on_hand == Decimal("10")


async def test_debt_aging_is_org_scoped(org_factory) -> None:
    org_a = await org_factory("AgingA")
    org_b = await org_factory("AgingB")

    product_a = await _create_product(org_a, sku="AGING-A")
    customer_a = await _create_customer(org_a)
    await _add_opening_stock(org_a, product_a, "10")
    await _post_sale(
        org_a,
        uuid.uuid4(),
        _sale_payload(customer_a, product_a, method="receivable"),
    )

    supplier_a = await _create_supplier(org_a, name="Supplier A")
    await _post_expense(
        org_a,
        amount="25.00",
        method="payable",
        supplier_id=supplier_a,
    )

    supplier_b = await _create_supplier(org_b, name="Supplier B")
    await _post_expense(
        org_b,
        amount="60.00",
        method="payable",
        supplier_id=supplier_b,
    )

    async with SessionLocal() as session:
        async with session.begin():
            await _bind(session, org_a)
            receivable_a = await ledger_service.debt_aging(
                session, org_a["org_id"], item_type="customer_receivable"
            )
            payable_a = await ledger_service.debt_aging(
                session, org_a["org_id"], item_type="supplier_payable"
            )
            with pytest.raises(ValueError):
                await ledger_service.debt_aging(
                    session, org_a["org_id"], item_type="not_a_debt"
                )

        async with session.begin():
            await _bind(session, org_b)
            receivable_b = await ledger_service.debt_aging(
                session, org_b["org_id"], item_type="customer_receivable"
            )
            payable_b = await ledger_service.debt_aging(
                session, org_b["org_id"], item_type="supplier_payable"
            )

    assert receivable_a.total_outstanding == Decimal("40")
    assert payable_a.total_outstanding == Decimal("25")
    assert receivable_b.total_outstanding == Decimal("0")
    assert payable_b.total_outstanding == Decimal("60")

    current_a = {bucket.bucket: bucket for bucket in receivable_a.buckets}["0-30"]
    assert current_a.item_count == 1
    assert current_a.outstanding_amount == Decimal("40")
    current_payable_a = {bucket.bucket: bucket for bucket in payable_a.buckets}[
        "0-30"
    ]
    assert current_payable_a.item_count == 1
    assert current_payable_a.outstanding_amount == Decimal("25")
    current_payable_b = {bucket.bucket: bucket for bucket in payable_b.buckets}[
        "0-30"
    ]
    assert current_payable_b.item_count == 1
    assert current_payable_b.outstanding_amount == Decimal("60")
    assert sum(
        (bucket.outstanding_amount for bucket in payable_a.buckets), Decimal("0")
    ) == payable_a.total_outstanding


# ── Settlements ──────────────────────────────────────────────


async def test_receivable_settlement_partial_receipt_and_replay(org_factory) -> None:
    org = await org_factory("SettlementReceivable")
    product_id = await _create_product(org)
    customer_id = await _create_customer(org)
    await _add_opening_stock(org, product_id, "10")

    sale = await _post_sale(
        org,
        uuid.uuid4(),
        _sale_payload(customer_id, product_id, method="receivable"),
    )
    assert sale.open_item_id is not None

    key = uuid.uuid4()
    async with SessionLocal() as session:
        async with session.begin():
            settlement = await posting_service.settle_open_item(
                session,
                org_id=org["org_id"],
                open_item_id=sale.open_item_id,
                amount="15.00",
                effective_at=datetime(2026, 1, 20, tzinfo=timezone.utc),
                idempotency_key=key,
                currency="NGN",
                issue_receipt=True,
                notes="Part payment",
                requested_by=org["user_id"],
            )

    assert settlement.replayed is False
    assert settlement.open_item_id == sale.open_item_id
    assert settlement.allocation_id is not None
    assert settlement.receipt_id is not None

    async with SessionLocal() as session:
        async with session.begin():
            await _bind(session, org)
            item = await ledger_service.get_open_item(
                session, org["org_id"], sale.open_item_id
            )
            lines = await ledger_service.get_journal_lines(
                session, org["org_id"], settlement.journal_entry_id
            )
            receipt = await ledger_service.get_receipt(
                session, org["org_id"], settlement.receipt_id
            )

    assert item is not None
    assert item.outstanding_amount == Decimal("25")
    by_code = {line.account_code: line for line in lines}
    assert by_code["1000"].debit == Decimal("15")
    assert by_code["1000"].credit == Decimal("0")
    assert by_code["1100"].debit == Decimal("0")
    assert by_code["1100"].credit == Decimal("15")
    assert receipt is not None
    assert receipt.receipt_type == "receipt"
    assert receipt.total_amount == Decimal("15")

    after = await _counts(org)
    replay = await _settle(
        org,
        sale.open_item_id,
        amount="15.00",
        key=key,
        issue_receipt=True,
        currency="NGN",
    )
    assert replay.replayed is True
    assert replay.journal_entry_id == settlement.journal_entry_id
    assert replay.allocation_id == settlement.allocation_id
    assert replay.receipt_id == settlement.receipt_id
    assert await _counts(org) == after

    with pytest.raises(PostingValidationError) as excinfo:
        await _settle(org, sale.open_item_id, amount="26.00", key=uuid.uuid4())
    assert excinfo.value.code == "invalid_settlement_amount"
    assert await _counts(org) == after


async def test_payable_settlement_and_receipt_guard(org_factory) -> None:
    org = await org_factory("SettlementPayable")
    supplier_id = await _create_supplier(org)
    expense = await _post_expense(
        org,
        amount="25.00",
        method="payable",
        supplier_id=supplier_id,
    )
    assert expense.open_item_id is not None
    before = await _counts(org)

    with pytest.raises(PostingValidationError) as excinfo:
        await _settle(
            org,
            expense.open_item_id,
            amount="25.00",
            issue_receipt=True,
            currency="NGN",
        )
    assert excinfo.value.code == "invalid_receipt_request"
    assert await _counts(org) == before

    settlement = await _settle(
        org, expense.open_item_id, amount="25.00", effective_at="2026-01-21T12:00:00Z"
    )
    assert settlement.receipt_id is None
    assert settlement.allocation_id is not None

    async with SessionLocal() as session:
        async with session.begin():
            await _bind(session, org)
            item = await ledger_service.get_open_item(
                session, org["org_id"], expense.open_item_id
            )
            lines = await ledger_service.get_journal_lines(
                session, org["org_id"], settlement.journal_entry_id
            )
    assert item is not None
    assert item.outstanding_amount == Decimal("0")
    by_code = {line.account_code: line for line in lines}
    assert by_code["2000"].debit == Decimal("25")
    assert by_code["1000"].credit == Decimal("25")


async def test_reverse_settlement_reopens_item_and_is_idempotent(org_factory) -> None:
    org = await org_factory("SettlementReversal")
    product_id = await _create_product(org)
    customer_id = await _create_customer(org)
    await _add_opening_stock(org, product_id, "10")

    sale = await _post_sale(
        org,
        uuid.uuid4(),
        _sale_payload(customer_id, product_id, method="receivable"),
    )
    settlement = await _settle(
        org, sale.open_item_id, amount="40.00", effective_at="2026-01-20T12:00:00Z"
    )
    assert settlement.allocation_id is not None

    key = uuid.uuid4()
    async with SessionLocal() as session:
        async with session.begin():
            reversal = await posting_service.reverse_settlement(
                session,
                org_id=org["org_id"],
                allocation_id=settlement.allocation_id,
                effective_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
                idempotency_key=key,
                requested_by=org["user_id"],
            )

    assert reversal.replayed is False
    assert reversal.open_item_id == sale.open_item_id
    assert reversal.allocation_id is not None

    async with SessionLocal() as session:
        async with session.begin():
            await _bind(session, org)
            item = await ledger_service.get_open_item(
                session, org["org_id"], sale.open_item_id
            )
            lines = await ledger_service.get_journal_lines(
                session, org["org_id"], reversal.journal_entry_id
            )
            allocation = (
                await session.execute(
                    text(
                        "SELECT amount, reversal_of_id FROM ledger.payment_allocations "
                        "WHERE id = CAST(:id AS uuid)"
                    ),
                    {"id": reversal.allocation_id},
                )
            ).one()
    assert item is not None
    assert item.outstanding_amount == Decimal("40")
    by_code = {line.account_code: line for line in lines}
    assert by_code["1100"].debit == Decimal("40")
    assert by_code["1000"].credit == Decimal("40")
    assert Decimal(allocation.amount) == Decimal("-40")
    assert allocation.reversal_of_id == settlement.allocation_id

    replay = await _reverse_settlement(org, settlement.allocation_id, key)
    assert replay.replayed is True
    assert replay.journal_entry_id == reversal.journal_entry_id
    assert replay.allocation_id == reversal.allocation_id

    with pytest.raises(PostingConflictError) as excinfo:
        await _reverse_settlement(org, settlement.allocation_id, uuid.uuid4())
    assert excinfo.value.code == "settlement_already_reversed"


async def _settle(
    org: dict,
    open_item_id: uuid.UUID,
    *,
    amount: str,
    key: uuid.UUID | None = None,
    effective_at: str = "2026-01-20T12:00:00Z",
    issue_receipt: bool = False,
    currency: str | None = None,
):
    async with SessionLocal() as session:
        async with session.begin():
            return await posting_service.settle_open_item(
                session,
                org_id=org["org_id"],
                open_item_id=open_item_id,
                amount=amount,
                effective_at=datetime.fromisoformat(
                    effective_at.replace("Z", "+00:00")
                ),
                idempotency_key=key or uuid.uuid4(),
                currency=currency,
                issue_receipt=issue_receipt,
                requested_by=org["user_id"],
            )


async def _reverse_settlement(
    org: dict,
    allocation_id: uuid.UUID,
    key: uuid.UUID,
):
    async with SessionLocal() as session:
        async with session.begin():
            return await posting_service.reverse_settlement(
                session,
                org_id=org["org_id"],
                allocation_id=allocation_id,
                idempotency_key=key,
                requested_by=org["user_id"],
            )
