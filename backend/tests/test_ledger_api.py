"""Ledger workspace read API tests.

Covers the chart of accounts, journal-entry filters, entry detail, and the
per-account general-ledger statement. Every test runs through the real API, so
the RBAC dependency, RLS-bound session, and org scoping are exercised too.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

REPORT_START = "2026-01-01"
REPORT_END = "2026-01-31"


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _d(value: object) -> Decimal:
    return Decimal(str(value))


async def _create_product(client, headers) -> dict:
    response = await client.post(
        "/api/v1/products",
        json={
            "sku": f"LDG-{uuid.uuid4().hex[:8]}",
            "name": "Ledger Widget",
            "unit": "each",
            "sale_price": "25.50",
            "cost": "10.00",
            "reorder_point": "0",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _create_customer(client, headers) -> dict:
    response = await client.post(
        "/api/v1/customers",
        json={"name": f"Ledger Customer {uuid.uuid4().hex[:6]}"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _create_opening_stock(client, headers, product_id: str) -> dict:
    response = await client.post(
        "/api/v1/inventory/opening-stock",
        json={
            "effective_at": "2026-01-01T09:00:00+00:00",
            "memo": "Opening stock",
            "lines": [
                {
                    "product_id": product_id,
                    "quantity": "10",
                    "unit_cost": "10.00",
                }
            ],
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _approve(client, headers, proposal_id: str) -> dict:
    response = await client.post(
        f"/api/v1/proposals/{proposal_id}/approve",
        json={"note": "approved in ledger test"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _create_sale(
    client, headers, *, customer_id: str, product_id: str
) -> dict:
    response = await client.post(
        "/api/v1/sales",
        json={
            "customer_id": customer_id,
            "currency": "NGN",
            "effective_at": "2026-01-18T10:00:00+00:00",
            "lines": [
                {
                    "product_id": product_id,
                    "quantity": "2",
                    "unit_price": "25.50",
                }
            ],
            "payment": {"method": "cash", "cash_account_code": "1000"},
            "idempotency_key": uuid.uuid4().hex,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _create_expense(client, headers) -> dict:
    response = await client.post(
        "/api/v1/expenses",
        json={
            "title": "Ledger rent",
            "amount": "10.00",
            "currency": "NGN",
            "effective_at": "2026-01-20T10:00:00+00:00",
            "category": "rent",
            "vendor": "Ledger Vendor",
            "description": "Ledger test expense",
            "payment": {"method": "cash", "cash_account_code": "1000"},
            "idempotency_key": uuid.uuid4().hex,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_ledger_accounts_journal_and_activity(
    client, register_user, make_member
) -> None:
    reg = await register_user(org_name="Ledger Tenant")
    h = _h(reg["access_token"])

    product = await _create_product(client, h)
    customer = await _create_customer(client, h)

    opening = await _create_opening_stock(client, h, product["id"])
    opening_entry = await _approve(
        client, h, opening["proposal"]["id"]
    )
    assert opening_entry["journal_entry_id"]

    sale = await _create_sale(
        client,
        h,
        customer_id=customer["id"],
        product_id=product["id"],
    )
    sale_entry = await _approve(client, h, sale["sale"]["id"])
    assert sale_entry["journal_entry_id"]

    expense = await _create_expense(client, h)
    expense_entry = await _approve(client, h, expense["expense"]["id"])
    assert expense_entry["journal_entry_id"]

    accounts_response = await client.get(
        "/api/v1/ledger/accounts", headers=h
    )
    assert accounts_response.status_code == 200, accounts_response.text
    accounts = {row["code"]: row for row in accounts_response.json()}
    assert {
        "1000",
        "1200",
        "3000",
        "4000",
        "5000",
        "6100",
    } <= accounts.keys()

    assert _d(accounts["1000"]["debit_total"]) == Decimal("51.00")
    assert _d(accounts["1000"]["credit_total"]) == Decimal("10.00")
    assert _d(accounts["1000"]["balance"]) == Decimal("41.00")
    assert _d(accounts["1200"]["balance"]) == Decimal("80.00")
    assert _d(accounts["3000"]["balance"]) == Decimal("100.00")
    assert _d(accounts["4000"]["balance"]) == Decimal("51.00")
    assert _d(accounts["5000"]["balance"]) == Decimal("20.00")
    assert _d(accounts["6100"]["balance"]) == Decimal("10.00")

    revenue_response = await client.get(
        "/api/v1/ledger/accounts",
        params={"account_type": "revenue"},
        headers=h,
    )
    assert revenue_response.status_code == 200, revenue_response.text
    revenue_codes = {row["code"] for row in revenue_response.json()}
    assert "4000" in revenue_codes
    assert all(
        row["account_type"] == "revenue"
        for row in revenue_response.json()
    )

    search_response = await client.get(
        "/api/v1/ledger/accounts",
        params={"q": "Cash"},
        headers=h,
    )
    assert search_response.status_code == 200, search_response.text
    assert [row["code"] for row in search_response.json()] == ["1000"]

    entries_response = await client.get(
        "/api/v1/ledger/journal-entries", headers=h
    )
    assert entries_response.status_code == 200, entries_response.text
    entries = entries_response.json()
    assert entries["total"] == 3
    assert len(entries["items"]) == 3
    assert {
        item["proposal_type"] for item in entries["items"]
    } == {"opening_stock", "sale", "expense"}
    assert all(
        item["source_type"] == "proposal" for item in entries["items"]
    )
    assert all(
        item["total_debit"] == item["total_credit"]
        for item in entries["items"]
    )

    sale_entries_response = await client.get(
        "/api/v1/ledger/journal-entries",
        params={"proposal_type": "sale"},
        headers=h,
    )
    assert sale_entries_response.status_code == 200, (
        sale_entries_response.text
    )
    sale_items = sale_entries_response.json()["items"]
    assert len(sale_items) == 1
    assert sale_items[0]["source_id"] == sale["sale"]["id"]

    date_filtered = await client.get(
        "/api/v1/ledger/journal-entries",
        params={"start": "2026-01-19", "end": REPORT_END},
        headers=h,
    )
    assert date_filtered.status_code == 200, date_filtered.text
    assert date_filtered.json()["total"] == 1
    assert date_filtered.json()["items"][0]["proposal_type"] == "expense"

    memo_filtered = await client.get(
        "/api/v1/ledger/journal-entries",
        params={"q": "Opening"},
        headers=h,
    )
    assert memo_filtered.status_code == 200, memo_filtered.text
    assert memo_filtered.json()["total"] == 1
    assert (
        memo_filtered.json()["items"][0]["proposal_type"]
        == "opening_stock"
    )

    posted_filtered = await client.get(
        "/api/v1/ledger/journal-entries",
        params={"status": "posted"},
        headers=h,
    )
    assert posted_filtered.status_code == 200, posted_filtered.text
    assert posted_filtered.json()["total"] == 3

    source_filtered = await client.get(
        "/api/v1/ledger/journal-entries",
        params={"source_type": "proposal"},
        headers=h,
    )
    assert source_filtered.status_code == 200, source_filtered.text
    assert source_filtered.json()["total"] == 3

    revenue_account_id = accounts["4000"]["id"]
    by_account = await client.get(
        "/api/v1/ledger/journal-entries",
        params={"account_id": revenue_account_id},
        headers=h,
    )
    assert by_account.status_code == 200, by_account.text
    assert by_account.json()["total"] == 1
    assert by_account.json()["items"][0]["id"] == sale_entry["journal_entry_id"]

    detail_response = await client.get(
        f"/api/v1/ledger/journal-entries/"
        f"{sale_entry['journal_entry_id']}",
        headers=h,
    )
    assert detail_response.status_code == 200, detail_response.text
    detail = detail_response.json()
    assert detail["proposal_type"] == "sale"
    assert detail["source_id"] == sale["sale"]["id"]
    assert len(detail["lines"]) == 4
    assert sum(
        (_d(line["debit"]) for line in detail["lines"]), Decimal("0")
    ) == Decimal("71.00")
    assert sum(
        (_d(line["credit"]) for line in detail["lines"]), Decimal("0")
    ) == Decimal("71.00")
    assert detail["documents"]["receipt_id"] is not None
    assert detail["documents"]["receipt_type"] == "receipt"

    cash_id = accounts["1000"]["id"]
    activity_response = await client.get(
        f"/api/v1/ledger/accounts/{cash_id}/activity",
        params={"start": REPORT_START, "end": REPORT_END},
        headers=h,
    )
    assert activity_response.status_code == 200, activity_response.text
    activity = activity_response.json()
    assert activity["account"]["code"] == "1000"
    assert _d(activity["opening_balance"]) == Decimal("0")
    assert _d(activity["debit_total"]) == Decimal("51.00")
    assert _d(activity["credit_total"]) == Decimal("10.00")
    assert _d(activity["closing_balance"]) == Decimal("41.00")
    assert activity["total"] == 2
    assert [_d(line["running_balance"]) for line in activity["lines"]] == [
        Decimal("51.00"),
        Decimal("41.00"),
    ]

    later_activity = await client.get(
        f"/api/v1/ledger/accounts/{cash_id}/activity",
        params={"start": "2026-01-19", "end": REPORT_END},
        headers=h,
    )
    assert later_activity.status_code == 200, later_activity.text
    later = later_activity.json()
    assert _d(later["opening_balance"]) == Decimal("51.00")
    assert _d(later["closing_balance"]) == Decimal("41.00")
    assert later["total"] == 1

    paged_activity = await client.get(
        f"/api/v1/ledger/accounts/{cash_id}/activity",
        params={
            "start": REPORT_START,
            "end": REPORT_END,
            "limit": 1,
            "offset": 1,
        },
        headers=h,
    )
    assert paged_activity.status_code == 200, paged_activity.text
    paged = paged_activity.json()
    assert paged["total"] == 2
    assert len(paged["lines"]) == 1
    assert _d(paged["lines"][0]["running_balance"]) == Decimal("41.00")

    revenue_activity = await client.get(
        f"/api/v1/ledger/accounts/{revenue_account_id}/activity",
        params={"start": REPORT_START, "end": REPORT_END},
        headers=h,
    )
    assert revenue_activity.status_code == 200, revenue_activity.text
    revenue = revenue_activity.json()
    assert _d(revenue["closing_balance"]) == Decimal("-51.00")

    periods_response = await client.get(
        "/api/v1/ledger/periods", headers=h
    )
    assert periods_response.status_code == 200, periods_response.text
    assert isinstance(periods_response.json(), list)

    invalid_window = await client.get(
        "/api/v1/ledger/journal-entries",
        params={"start": "2026-02-01", "end": REPORT_START},
        headers=h,
    )
    assert invalid_window.status_code == 422
    assert invalid_window.json()["code"] == "validation_error"

    viewer = await make_member(reg["active_org_id"], "viewer")
    viewer_accounts = await client.get(
        "/api/v1/ledger/accounts", headers=_h(viewer["token"])
    )
    assert viewer_accounts.status_code == 200, viewer_accounts.text
    viewer_entries = await client.get(
        "/api/v1/ledger/journal-entries", headers=_h(viewer["token"])
    )
    assert viewer_entries.status_code == 200, viewer_entries.text
    assert viewer_entries.json()["total"] == 3


async def test_ledger_is_tenant_scoped(client, register_user) -> None:
    first = await register_user(org_name="Ledger Tenant A")
    first_h = _h(first["access_token"])
    product = await _create_product(client, first_h)
    customer = await _create_customer(client, first_h)
    opening = await _create_opening_stock(client, first_h, product["id"])
    await _approve(client, first_h, opening["proposal"]["id"])
    sale = await _create_sale(
        client,
        first_h,
        customer_id=customer["id"],
        product_id=product["id"],
    )
    sale_entry = await _approve(client, first_h, sale["sale"]["id"])

    first_accounts = await client.get(
        "/api/v1/ledger/accounts", headers=first_h
    )
    assert first_accounts.status_code == 200, first_accounts.text
    cash_id = {
        row["code"]: row["id"] for row in first_accounts.json()
    }["1000"]

    second = await register_user(org_name="Ledger Tenant B")
    second_h = _h(second["access_token"])

    entries = await client.get(
        "/api/v1/ledger/journal-entries", headers=second_h
    )
    assert entries.status_code == 200, entries.text
    assert entries.json()["total"] == 0

    foreign_entry = await client.get(
        f"/api/v1/ledger/journal-entries/"
        f"{sale_entry['journal_entry_id']}",
        headers=second_h,
    )
    assert foreign_entry.status_code == 404
    assert foreign_entry.json()["code"] == "not_found"

    foreign_activity = await client.get(
        f"/api/v1/ledger/accounts/{cash_id}/activity",
        headers=second_h,
    )
    assert foreign_activity.status_code == 404
    assert foreign_activity.json()["code"] == "not_found"
