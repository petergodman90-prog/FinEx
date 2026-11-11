"""Expense, supplier, debt, and settlement API tests."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _d(value: object) -> Decimal:
    return Decimal(str(value))


async def _create_supplier(client, headers, name="Acme Supplies") -> dict:
    response = await client.post(
        "/api/v1/suppliers",
        json={"name": name, "code": f"SUP-{uuid.uuid4().hex[:6]}"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _create_expense(
    client,
    headers,
    *,
    amount: str = "120.50",
    method: str = "cash",
    supplier_id: str | None = None,
    due_on: str | None = None,
    category: str = "rent",
    title: str = "Office rent",
    cash_account_code: str = "1000",
    idempotency_key: str | None = None,
    effective_at: str | None = None,
) -> dict:
    payment: dict = {
        "method": method,
        "cash_account_code": cash_account_code,
    }
    if supplier_id is not None:
        payment["supplier_id"] = supplier_id
    if due_on is not None:
        payment["due_on"] = due_on
    response = await client.post(
        "/api/v1/expenses",
        json={
            "title": title,
            "amount": amount,
            "currency": "NGN",
            "effective_at": effective_at or _now(),
            "category": category,
            "vendor": "Acme Supplies",
            "description": "Test expense",
            "payment": payment,
            "idempotency_key": idempotency_key or uuid.uuid4().hex,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _approve(client, headers, proposal_id: str) -> dict:
    response = await client.post(
        f"/api/v1/proposals/{proposal_id}/approve",
        json={"note": "approved in test"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_supplier_and_cash_expense_flow(client, register_user, make_member) -> None:
    reg = await register_user()
    h = _h(reg["access_token"])

    supplier = await _create_supplier(client, h)
    detail = await client.get(
        f"/api/v1/suppliers/{supplier['id']}", headers=h
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["name"] == "Acme Supplies"

    listed = await client.get("/api/v1/suppliers", headers=h)
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1

    key = uuid.uuid4().hex
    effective_at = _now()
    created = await _create_expense(
        client, h, idempotency_key=key, effective_at=effective_at
    )
    expense = created["expense"]
    assert created["created"] is True
    assert expense["status"] == "pending"
    assert expense["expense_account_code"] == "6100"
    assert expense["payment_method"] == "cash"
    assert expense["journal_entry_id"] is None

    replay = await _create_expense(
        client, h, idempotency_key=key, effective_at=effective_at
    )
    assert replay["created"] is False
    assert replay["expense"]["id"] == expense["id"]

    posted = await _approve(client, h, expense["id"])
    assert posted["status"] == "applied", posted["posting_error"]
    assert posted["journal_entry_id"] is not None
    assert posted["posted_at"] is not None

    fetched = await client.get(f"/api/v1/expenses/{expense['id']}", headers=h)
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["status"] == "applied"
    assert fetched.json()["journal_entry_id"] == posted["journal_entry_id"]

    listed_expenses = await client.get(
        "/api/v1/expenses", params={"status": "applied"}, headers=h
    )
    assert listed_expenses.status_code == 200, listed_expenses.text
    assert listed_expenses.json()["total"] == 1

    viewer = await make_member(reg["active_org_id"], "viewer")
    viewer_h = _h(viewer["token"])
    assert (
        await client.get("/api/v1/expenses", headers=viewer_h)
    ).status_code == 200
    denied = await client.post(
        "/api/v1/expenses",
        json={
            "title": "Denied",
            "amount": "1.00",
            "currency": "NGN",
            "effective_at": _now(),
            "category": "rent",
        },
        headers=viewer_h,
    )
    assert denied.status_code == 403

    bad_category = await client.post(
        "/api/v1/expenses",
        json={
            "title": "Bad category",
            "amount": "10.00",
            "currency": "NGN",
            "effective_at": _now(),
            "category": "not-a-category",
        },
        headers=h,
    )
    assert bad_category.status_code == 422

    missing_supplier = await client.post(
        "/api/v1/expenses",
        json={
            "title": "Payable without supplier",
            "amount": "10.00",
            "currency": "NGN",
            "effective_at": _now(),
            "category": "rent",
            "payment": {"method": "payable"},
        },
        headers=h,
    )
    assert missing_supplier.status_code == 422

    bad_cash = await _create_expense(
        client,
        h,
        title="Wrong cash account",
        amount="10.00",
        cash_account_code="4000",
    )
    bad_cash_posted = await _approve(
        client, h, bad_cash["expense"]["id"]
    )
    assert bad_cash_posted["status"] == "approved"
    assert (
        bad_cash_posted["posting_error"]["code"] == "invalid_cash_account"
    )


async def test_payable_expense_debt_and_settlement_flow(
    client, register_user
) -> None:
    reg = await register_user()
    h = _h(reg["access_token"])
    supplier = await _create_supplier(client, h, name="Power Utility Ltd")
    due_on = (datetime.now(timezone.utc) - timedelta(days=5)).date().isoformat()

    created = await _create_expense(
        client,
        h,
        title="Electricity bill",
        amount="100.00",
        method="payable",
        supplier_id=supplier["id"],
        due_on=due_on,
        category="utilities",
    )
    await _approve(client, h, created["expense"]["id"])

    debts = await client.get(
        "/api/v1/debts",
        params={"item_type": "supplier_payable", "overdue_only": "true"},
        headers=h,
    )
    assert debts.status_code == 200, debts.text
    body = debts.json()
    assert body["total"] == 1
    debt = body["items"][0]
    assert debt["counterparty_name"] == "Power Utility Ltd"
    assert debt["is_overdue"] is True
    assert _d(debt["outstanding_amount"]) == Decimal("100.00")

    summary = await client.get("/api/v1/debts/summary", headers=h)
    assert summary.status_code == 200, summary.text
    assert _d(summary.json()["total_payables"]) == Decimal("100")
    assert _d(summary.json()["overdue_payables"]) == Decimal("100")
    assert summary.json()["overdue_count"] == 1
    assert summary.json()["total_creditors"] == 1

    aging = await client.get(
        "/api/v1/debts/aging",
        params={"item_type": "supplier_payable"},
        headers=h,
    )
    assert aging.status_code == 200, aging.text
    assert _d(aging.json()["total_outstanding"]) == Decimal("100")

    detail = await client.get(f"/api/v1/debts/{debt['id']}", headers=h)
    assert detail.status_code == 200, detail.text
    assert detail.json()["id"] == debt["id"]

    history = await client.get(
        f"/api/v1/debts/{debt['id']}/settlements", headers=h
    )
    assert history.status_code == 200, history.text
    assert history.json()["total"] == 0

    settlement = await client.post(
        f"/api/v1/debts/{debt['id']}/settlements",
        json={
            "amount": "40.00",
            "currency": "NGN",
            "effective_at": _now(),
            "idempotency_key": uuid.uuid4().hex,
        },
        headers=h,
    )
    assert settlement.status_code == 200, settlement.text
    settlement_body = settlement.json()
    assert settlement_body["created"] is True
    assert settlement_body["proposal"]["status"] == "pending"
    assert settlement_body["proposal"]["proposal_type"] == "settlement"

    applied = await _approve(client, h, settlement_body["proposal"]["id"])
    assert applied["status"] == "applied"
    assert applied["journal_entry_id"] is not None

    after_partial = await client.get(f"/api/v1/debts/{debt['id']}", headers=h)
    assert _d(after_partial.json()["outstanding_amount"]) == Decimal("60.00")

    history = await client.get(
        f"/api/v1/debts/{debt['id']}/settlements", headers=h
    )
    assert history.json()["total"] == 1
    allocation = history.json()["items"][0]
    assert _d(allocation["amount"]) == Decimal("40")
    assert allocation["is_reversed"] is False

    overpay = await client.post(
        f"/api/v1/debts/{debt['id']}/settlements",
        json={
            "amount": "100.00",
            "currency": "NGN",
            "effective_at": _now(),
            "idempotency_key": uuid.uuid4().hex,
        },
        headers=h,
    )
    assert overpay.status_code == 422

    bad_receipt = await client.post(
        f"/api/v1/debts/{debt['id']}/settlements",
        json={
            "amount": "1.00",
            "currency": "NGN",
            "effective_at": _now(),
            "issue_receipt": True,
            "idempotency_key": uuid.uuid4().hex,
        },
        headers=h,
    )
    assert bad_receipt.status_code == 422

    bad_cash = await client.post(
        f"/api/v1/debts/{debt['id']}/settlements",
        json={
            "amount": "1.00",
            "currency": "NGN",
            "effective_at": _now(),
            "cash_account_code": "2000",
            "idempotency_key": uuid.uuid4().hex,
        },
        headers=h,
    )
    assert bad_cash.status_code == 200, bad_cash.text
    bad_cash_posted = await _approve(
        client, h, bad_cash.json()["proposal"]["id"]
    )
    assert bad_cash_posted["status"] == "approved"
    assert (
        bad_cash_posted["posting_error"]["code"] == "invalid_cash_account"
    )

    final = await client.post(
        f"/api/v1/debts/{debt['id']}/settlements",
        json={
            "amount": "60.00",
            "currency": "NGN",
            "effective_at": _now(),
            "idempotency_key": uuid.uuid4().hex,
        },
        headers=h,
    )
    assert final.status_code == 200, final.text
    await _approve(client, h, final.json()["proposal"]["id"])

    settled = await client.get(f"/api/v1/debts/{debt['id']}", headers=h)
    assert _d(settled.json()["outstanding_amount"]) == Decimal("0")

    outstanding = await client.get(
        "/api/v1/debts",
        params={"item_type": "supplier_payable"},
        headers=h,
    )
    assert outstanding.json()["total"] == 0

    all_debts = await client.get(
        "/api/v1/debts",
        params={"item_type": "supplier_payable", "outstanding_only": "false"},
        headers=h,
    )
    assert all_debts.json()["total"] == 1

    summary_after = await client.get("/api/v1/debts/summary", headers=h)
    assert _d(summary_after.json()["total_payables"]) == Decimal("0")
    assert summary_after.json()["total_creditors"] == 0


async def test_expenses_debts_tenant_isolation(client, register_user) -> None:
    first = await register_user(org_name="Expense Org A")
    first_h = _h(first["access_token"])
    supplier = await _create_supplier(client, first_h)
    created = await _create_expense(
        client,
        first_h,
        title="Other tenant payable",
        amount="25.00",
        method="payable",
        supplier_id=supplier["id"],
        category="utilities",
    )
    await _approve(client, first_h, created["expense"]["id"])
    debts = await client.get(
        "/api/v1/debts",
        params={"item_type": "supplier_payable"},
        headers=first_h,
    )
    assert debts.status_code == 200, debts.text
    debt = debts.json()["items"][0]

    second = await register_user(org_name="Expense Org B")
    second_h = _h(second["access_token"])

    assert (
        await client.get("/api/v1/suppliers", headers=second_h)
    ).json()["total"] == 0
    assert (
        await client.get("/api/v1/expenses", headers=second_h)
    ).json()["total"] == 0
    assert (
        await client.get("/api/v1/debts", headers=second_h)
    ).json()["total"] == 0
    assert (
        await client.get(
            f"/api/v1/expenses/{created['expense']['id']}", headers=second_h
        )
    ).status_code == 404
    assert (
        await client.get(f"/api/v1/debts/{debt['id']}", headers=second_h)
    ).status_code == 404
    assert (
        await client.get(
            f"/api/v1/suppliers/{supplier['id']}", headers=second_h
        )
    ).status_code == 404
    assert (
        await client.post(
            f"/api/v1/debts/{debt['id']}/settlements",
            json={
                "amount": "1.00",
                "currency": "NGN",
                "effective_at": _now(),
                "idempotency_key": uuid.uuid4().hex,
            },
            headers=second_h,
        )
    ).status_code == 404
