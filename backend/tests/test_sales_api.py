"""Sales and credit-note API tests."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _d(value: object) -> Decimal:
    return Decimal(str(value))


async def _create_product(
    client,
    headers,
    *,
    sale_price: str = "25.50",
    cost: str = "10.00",
) -> dict:
    response = await client.post(
        "/api/v1/products",
        json={
            "sku": f"SKU-{uuid.uuid4().hex[:8]}",
            "name": "Sales Widget",
            "unit": "each",
            "sale_price": sale_price,
            "cost": cost,
            "reorder_point": "0",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _create_customer(client, headers, name: str = "Sales Customer") -> dict:
    response = await client.post(
        "/api/v1/customers",
        json={"name": f"{name} {uuid.uuid4().hex[:6]}"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _create_opening_stock(
    client,
    headers,
    product_id: str,
    *,
    quantity: str = "10",
    unit_cost: str = "10.00",
) -> dict:
    response = await client.post(
        "/api/v1/inventory/opening-stock",
        json={
            "effective_at": _now(),
            "memo": "Opening stock",
            "lines": [
                {
                    "product_id": product_id,
                    "quantity": quantity,
                    "unit_cost": unit_cost,
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
        json={"note": "approved in test"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _create_sale(
    client,
    headers,
    *,
    customer_id: str,
    product_id: str,
    quantity: str = "2",
    method: str = "cash",
    unit_price: str | None = "25.50",
    due_on: str | None = None,
    issue_receipt: bool | None = None,
    cash_account_code: str = "1000",
    effective_at: str | None = None,
    idempotency_key: str | None = None,
    expected_status: int = 200,
):
    line: dict = {"product_id": product_id, "quantity": quantity}
    if unit_price is not None:
        line["unit_price"] = unit_price
    payment: dict = {
        "method": method,
        "cash_account_code": cash_account_code,
    }
    if due_on is not None:
        payment["due_on"] = due_on
    if issue_receipt is not None:
        payment["issue_receipt"] = issue_receipt
    response = await client.post(
        "/api/v1/sales",
        json={
            "customer_id": customer_id,
            "currency": "ngn",
            "effective_at": effective_at or _now(),
            "lines": [line],
            "payment": payment,
            "idempotency_key": idempotency_key or uuid.uuid4().hex,
        },
        headers=headers,
    )
    assert response.status_code == expected_status, response.text
    return response


async def _create_credit_note(
    client,
    headers,
    *,
    product_id: str,
    original_receipt_id: str,
    quantity: str = "1",
    unit_price: str = "25.50",
    customer_id: str | None = None,
    currency: str | None = None,
    restock: bool = True,
    cash_account_code: str = "1000",
    idempotency_key: str | None = None,
    expected_status: int = 200,
):
    line: dict = {
        "product_id": product_id,
        "quantity": quantity,
        "unit_price": unit_price,
    }
    payload: dict = {
        "effective_at": _now(),
        "lines": [line],
        "original_receipt_id": original_receipt_id,
        "restock": restock,
        "cash_account_code": cash_account_code,
        "idempotency_key": idempotency_key or uuid.uuid4().hex,
    }
    if customer_id is not None:
        payload["customer_id"] = customer_id
    if currency is not None:
        payload["currency"] = currency
    response = await client.post(
        "/api/v1/sales/credit-notes", json=payload, headers=headers
    )
    assert response.status_code == expected_status, response.text
    return response


async def test_cash_sale_posts_receipt_stock_and_list(client, register_user) -> None:
    reg = await register_user()
    h = _h(reg["access_token"])
    product = await _create_product(client, h)
    customer = await _create_customer(client, h)
    opening = await _create_opening_stock(client, h, product["id"])
    await _approve(client, h, opening["proposal"]["id"])

    key = uuid.uuid4().hex
    effective_at = _now()
    response = await _create_sale(
        client,
        h,
        customer_id=customer["id"],
        product_id=product["id"],
        effective_at=effective_at,
        idempotency_key=key,
    )
    body = response.json()
    sale = body["sale"]
    assert body["created"] is True
    assert sale["proposal_type"] == "sale"
    assert sale["status"] == "pending"
    assert _d(sale["amount"]) == Decimal("51.00")
    assert sale["currency"] == "NGN"
    assert sale["customer_id"] == customer["id"]
    assert sale["customer_name"] == customer["name"]
    assert sale["lines"][0]["product_id"] == product["id"]
    assert sale["lines"][0]["sku"] == product["sku"]
    assert sale["lines"][0]["name"] == product["name"]
    assert _d(sale["lines"][0]["unit_price"]) == Decimal("25.50")
    assert _d(sale["lines"][0]["unit_cost"]) == Decimal("10.00")
    assert _d(sale["lines"][0]["line_total"]) == Decimal("51.00")
    assert sale["payment"]["method"] == "cash"
    assert sale["payment"]["issue_receipt"] is True
    assert sale["journal_entry_id"] is None
    assert sale["receipt_id"] is None
    assert sale["open_item_id"] is None

    replay = await _create_sale(
        client,
        h,
        customer_id=customer["id"],
        product_id=product["id"],
        effective_at=effective_at,
        idempotency_key=key,
    )
    assert replay.json()["created"] is False
    assert replay.json()["sale"]["id"] == sale["id"]

    posted = await _approve(client, h, sale["id"])
    assert posted["status"] == "applied", posted["posting_error"]
    assert posted["journal_entry_id"] is not None

    detail = await client.get(f"/api/v1/sales/{sale['id']}", headers=h)
    assert detail.status_code == 200, detail.text
    applied = detail.json()
    assert applied["status"] == "applied"
    assert applied["receipt_id"] is not None
    assert applied["receipt_number"] > 0
    assert applied["journal_entry_id"] == posted["journal_entry_id"]
    assert applied["open_item_id"] is None

    listed = await client.get("/api/v1/sales", headers=h)
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == sale["id"]

    level = await client.get(
        f"/api/v1/inventory/stock-levels/{product['id']}", headers=h
    )
    assert _d(level.json()["quantity_on_hand"]) == Decimal("8")


async def test_receivable_sale_and_credit_note(client, register_user) -> None:
    reg = await register_user()
    h = _h(reg["access_token"])
    product = await _create_product(client, h)
    customer = await _create_customer(client, h)
    opening = await _create_opening_stock(client, h, product["id"])
    await _approve(client, h, opening["proposal"]["id"])

    receivable = await _create_sale(
        client,
        h,
        customer_id=customer["id"],
        product_id=product["id"],
        method="receivable",
        due_on="2027-01-31",
    )
    receivable_sale = receivable.json()["sale"]
    posted = await _approve(client, h, receivable_sale["id"])
    assert posted["status"] == "applied", posted["posting_error"]

    receivable_detail = await client.get(
        f"/api/v1/sales/{receivable_sale['id']}", headers=h
    )
    assert receivable_detail.status_code == 200, receivable_detail.text
    assert receivable_detail.json()["receipt_id"] is None
    assert receivable_detail.json()["open_item_id"] is not None
    assert receivable_detail.json()["payment"]["due_on"] == "2027-01-31"

    paid = await _create_sale(
        client,
        h,
        customer_id=customer["id"],
        product_id=product["id"],
        quantity="4",
        unit_price="25.50",
    )
    paid_sale = paid.json()["sale"]
    await _approve(client, h, paid_sale["id"])
    paid_detail = await client.get(
        f"/api/v1/sales/{paid_sale['id']}", headers=h
    )
    receipt_id = paid_detail.json()["receipt_id"]
    assert receipt_id is not None

    before = await client.get(
        f"/api/v1/inventory/stock-levels/{product['id']}", headers=h
    )
    credit = await _create_credit_note(
        client,
        h,
        product_id=product["id"],
        original_receipt_id=receipt_id,
    )
    body = credit.json()
    note = body["credit_note"]
    assert body["created"] is True
    assert note["proposal_type"] == "credit_note"
    assert note["status"] == "pending"
    assert note["original_receipt_id"] == receipt_id
    assert note["customer_id"] == customer["id"]
    assert note["customer_name"] == customer["name"]
    assert note["currency"] == "NGN"
    assert _d(note["amount"]) == Decimal("25.50")
    assert note["restock"] is True
    assert _d(note["lines"][0]["unit_cost"]) == Decimal("10.00")
    assert note["credit_note_id"] is None

    credit_posted = await _approve(client, h, note["id"])
    assert credit_posted["status"] == "applied", credit_posted["posting_error"]

    credit_detail = await client.get(
        f"/api/v1/sales/credit-notes/{note['id']}", headers=h
    )
    assert credit_detail.status_code == 200, credit_detail.text
    applied_note = credit_detail.json()
    assert applied_note["status"] == "applied"
    assert applied_note["credit_note_id"] is not None
    assert applied_note["credit_note_number"] > 0
    assert applied_note["journal_entry_id"] == credit_posted["journal_entry_id"]

    notes = await client.get("/api/v1/sales/credit-notes", headers=h)
    assert notes.status_code == 200, notes.text
    assert notes.json()["total"] == 1
    assert notes.json()["items"][0]["id"] == note["id"]

    after = await client.get(
        f"/api/v1/inventory/stock-levels/{product['id']}", headers=h
    )
    assert _d(after.json()["quantity_on_hand"]) == _d(
        before.json()["quantity_on_hand"]
    ) + Decimal("1")

    other_product = await _create_product(client, h)
    unrelated = await _create_credit_note(
        client,
        h,
        product_id=other_product["id"],
        original_receipt_id=receipt_id,
        expected_status=422,
    )
    assert unrelated.json()["code"] == "validation_error"

    over_quantity = await _create_credit_note(
        client,
        h,
        product_id=product["id"],
        original_receipt_id=receipt_id,
        quantity="5",
        expected_status=422,
    )
    assert over_quantity.json()["code"] == "validation_error"

    over_credit = await _create_credit_note(
        client,
        h,
        product_id=product["id"],
        original_receipt_id=receipt_id,
        quantity="1",
        unit_price="100.00",
        restock=False,
        expected_status=422,
    )
    assert over_credit.json()["code"] == "validation_error"

    bad_cash = await _create_credit_note(
        client,
        h,
        product_id=product["id"],
        original_receipt_id=receipt_id,
        cash_account_code="4000",
    )
    bad_cash_note = bad_cash.json()["credit_note"]
    bad_cash_posted = await _approve(client, h, bad_cash_note["id"])
    assert bad_cash_posted["status"] == "approved"
    assert (
        bad_cash_posted["posting_error"]["code"] == "invalid_cash_account"
    )


async def test_sales_validation_and_rbac(
    client, register_user, make_member
) -> None:
    reg = await register_user()
    h = _h(reg["access_token"])
    product = await _create_product(client, h)
    customer = await _create_customer(client, h)
    opening = await _create_opening_stock(client, h, product["id"])
    await _approve(client, h, opening["proposal"]["id"])

    missing_product = await _create_sale(
        client,
        h,
        customer_id=customer["id"],
        product_id=str(uuid.uuid4()),
        expected_status=422,
    )
    assert missing_product.json()["code"] == "validation_error"

    duplicate_lines = await client.post(
        "/api/v1/sales",
        json={
            "customer_id": customer["id"],
            "currency": "NGN",
            "effective_at": _now(),
            "lines": [
                {
                    "product_id": product["id"],
                    "quantity": "1",
                    "unit_price": "25.50",
                },
                {
                    "product_id": product["id"],
                    "quantity": "2",
                    "unit_price": "25.50",
                },
            ],
        },
        headers=h,
    )
    assert duplicate_lines.status_code == 422

    cost_override = await client.post(
        "/api/v1/sales",
        json={
            "customer_id": customer["id"],
            "currency": "NGN",
            "effective_at": _now(),
            "lines": [
                {
                    "product_id": product["id"],
                    "quantity": "1",
                    "unit_price": "25.50",
                    "unit_cost": "0.01",
                }
            ],
        },
        headers=h,
    )
    assert cost_override.status_code == 422

    receipt_on_receivable = await _create_sale(
        client,
        h,
        customer_id=customer["id"],
        product_id=product["id"],
        method="receivable",
        issue_receipt=True,
        expected_status=422,
    )
    assert receipt_on_receivable.status_code == 422

    too_precise = await _create_sale(
        client,
        h,
        customer_id=customer["id"],
        product_id=product["id"],
        quantity="1",
        unit_price="1.005",
        expected_status=422,
    )
    assert too_precise.json()["code"] == "validation_error"

    no_stock_product = await _create_product(client, h)
    insufficient = await _create_sale(
        client,
        h,
        customer_id=customer["id"],
        product_id=no_stock_product["id"],
        quantity="1",
        expected_status=422,
    )
    assert insufficient.json()["code"] == "validation_error"

    no_receipt_or_customer = await client.post(
        "/api/v1/sales/credit-notes",
        json={
            "effective_at": _now(),
            "lines": [{"quantity": "1", "unit_price": "5.00"}],
        },
        headers=h,
    )
    assert no_receipt_or_customer.status_code == 422

    unlinked_restock = await client.post(
        "/api/v1/sales/credit-notes",
        json={
            "effective_at": _now(),
            "customer_id": customer["id"],
            "currency": "NGN",
            "restock": True,
            "lines": [
                {
                    "product_id": product["id"],
                    "quantity": "1",
                    "unit_price": "5.00",
                }
            ],
        },
        headers=h,
    )
    assert unlinked_restock.status_code == 422
    assert "original_receipt_id" in unlinked_restock.text

    # A service-only credit note remains valid without a receipt as long as it
    # does not attempt to restock inventory.
    unlinked_service = await client.post(
        "/api/v1/sales/credit-notes",
        json={
            "effective_at": _now(),
            "customer_id": customer["id"],
            "currency": "NGN",
            "restock": False,
            "lines": [{"quantity": "1", "unit_price": "5.00"}],
        },
        headers=h,
    )
    assert unlinked_service.status_code == 200, unlinked_service.text
    assert unlinked_service.json()["credit_note"]["status"] == "pending"
    assert unlinked_service.json()["credit_note"]["restock"] is False

    credit_cost_override = await client.post(
        "/api/v1/sales/credit-notes",
        json={
            "effective_at": _now(),
            "customer_id": customer["id"],
            "currency": "NGN",
            "restock": False,
            "lines": [
                {
                    "quantity": "1",
                    "unit_price": "5.00",
                    "unit_cost": "0.01",
                }
            ],
        },
        headers=h,
    )
    assert credit_cost_override.status_code == 422

    bad_cash_sale = await _create_sale(
        client,
        h,
        customer_id=customer["id"],
        product_id=product["id"],
        cash_account_code="4000",
    )
    bad_cash_posted = await _approve(
        client, h, bad_cash_sale.json()["sale"]["id"]
    )
    assert bad_cash_posted["status"] == "approved"
    assert bad_cash_posted["posting_error"]["code"] == "invalid_cash_account"

    viewer = await make_member(reg["active_org_id"], "viewer")
    viewer_h = _h(viewer["token"])
    assert (await client.get("/api/v1/sales", headers=viewer_h)).status_code == 200
    denied = await client.post(
        "/api/v1/sales",
        json={
            "customer_id": customer["id"],
            "currency": "NGN",
            "effective_at": _now(),
            "lines": [
                {
                    "product_id": product["id"],
                    "quantity": "1",
                    "unit_price": "25.50",
                }
            ],
        },
        headers=viewer_h,
    )
    assert denied.status_code == 403


async def test_sales_tenant_isolation(client, register_user) -> None:
    first = await register_user(org_name="Sales Org A")
    first_h = _h(first["access_token"])
    product = await _create_product(client, first_h)
    customer = await _create_customer(client, first_h)
    opening = await _create_opening_stock(client, first_h, product["id"])
    await _approve(client, first_h, opening["proposal"]["id"])
    sale = (
        await _create_sale(
            client,
            first_h,
            customer_id=customer["id"],
            product_id=product["id"],
        )
    ).json()["sale"]

    second = await register_user(org_name="Sales Org B")
    second_h = _h(second["access_token"])

    assert (
        await client.get(f"/api/v1/sales/{sale['id']}", headers=second_h)
    ).status_code == 404
    sales = await client.get("/api/v1/sales", headers=second_h)
    assert sales.status_code == 200, sales.text
    assert sales.json()["total"] == 0
    notes = await client.get("/api/v1/sales/credit-notes", headers=second_h)
    assert notes.status_code == 200, notes.text
    assert notes.json()["total"] == 0
