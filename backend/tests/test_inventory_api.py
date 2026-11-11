"""Product master and inventory API tests."""

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
    sku: str | None = None,
    name: str = "Widget",
    sale_price: str = "25.50",
    cost: str = "10.00",
    reorder_point: str = "5",
    unit: str = "each",
) -> dict:
    response = await client.post(
        "/api/v1/products",
        json={
            "sku": sku or f"SKU-{uuid.uuid4().hex[:8]}",
            "name": name,
            "unit": unit,
            "sale_price": sale_price,
            "cost": cost,
            "reorder_point": reorder_point,
        },
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
    unit_cost: str | None = "10.00",
    memo: str = "Opening count",
    idempotency_key: str | None = None,
    effective_at: str | None = None,
) -> dict:
    line: dict = {"product_id": product_id, "quantity": quantity}
    if unit_cost is not None:
        line["unit_cost"] = unit_cost
    response = await client.post(
        "/api/v1/inventory/opening-stock",
        json={
            "effective_at": effective_at or _now(),
            "memo": memo,
            "lines": [line],
            "idempotency_key": idempotency_key or uuid.uuid4().hex,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _create_adjustment(
    client,
    headers,
    product_id: str,
    *,
    direction: str = "out",
    quantity: str = "3",
    unit_cost: str | None = "10.00",
    reason: str = "Damaged goods",
    idempotency_key: str | None = None,
) -> dict:
    line: dict = {"product_id": product_id, "quantity": quantity}
    if unit_cost is not None:
        line["unit_cost"] = unit_cost
    response = await client.post(
        "/api/v1/inventory/adjustments",
        json={
            "direction": direction,
            "effective_at": _now(),
            "reason": reason,
            "lines": [line],
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


async def test_product_master_crud_and_stock_reads(
    client, register_user, make_member
) -> None:
    reg = await register_user()
    h = _h(reg["access_token"])
    sku = f"SKU-{uuid.uuid4().hex[:8]}"

    product = await _create_product(client, h, sku=sku)
    assert product["sku"] == sku
    assert product["unit"] == "each"
    assert _d(product["current_sale_price"]) == Decimal("25.50")
    assert _d(product["current_cost"]) == Decimal("10")
    assert _d(product["reorder_point"]) == Decimal("5")
    assert product["is_active"] is True

    duplicate = await client.post(
        "/api/v1/products",
        json={
            "sku": sku,
            "name": "Duplicate",
            "sale_price": "1.00",
        },
        headers=h,
    )
    assert duplicate.status_code == 409, duplicate.text

    listed = await client.get(
        "/api/v1/products", params={"q": sku}, headers=h
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == product["id"]

    updated = await client.patch(
        f"/api/v1/products/{product['id']}",
        json={"sale_price": "30.00", "reorder_point": "10"},
        headers=h,
    )
    assert updated.status_code == 200, updated.text
    assert _d(updated.json()["current_sale_price"]) == Decimal("30")
    assert _d(updated.json()["reorder_point"]) == Decimal("10")

    empty_update = await client.patch(
        f"/api/v1/products/{product['id']}", json={}, headers=h
    )
    assert empty_update.status_code == 422
    null_update = await client.patch(
        f"/api/v1/products/{product['id']}",
        json={"sale_price": None},
        headers=h,
    )
    assert null_update.status_code == 422

    levels = await client.get(
        "/api/v1/inventory/stock-levels", params={"q": sku}, headers=h
    )
    assert levels.status_code == 200, levels.text
    body = levels.json()
    assert body["total"] == 1
    assert _d(body["items"][0]["quantity_on_hand"]) == Decimal("0")
    assert body["items"][0]["needs_reorder"] is True

    with_stock = await _create_product(
        client, h, name="Stocked item", reorder_point="2"
    )
    await _create_opening_stock(
        client, h, product["id"], quantity="4", unit_cost="10"
    )
    pending_approval = await client.get(
        "/api/v1/inventory/opening-stock", headers=h
    )
    assert pending_approval.status_code == 200, pending_approval.text
    proposal_id = pending_approval.json()["items"][0]["id"]
    await _approve(client, h, proposal_id)

    levels = await client.get("/api/v1/inventory/stock-levels", headers=h)
    assert levels.status_code == 200, levels.text
    rows = {row["product_id"]: row for row in levels.json()["items"]}
    assert set(rows) == {product["id"], with_stock["id"]}
    assert _d(rows[product["id"]]["quantity_on_hand"]) == Decimal("4")
    assert rows[product["id"]]["needs_reorder"] is True
    assert _d(rows[with_stock["id"]]["quantity_on_hand"]) == Decimal("0")
    assert rows[with_stock["id"]]["needs_reorder"] is True

    reorder_only = await client.get(
        "/api/v1/inventory/stock-levels",
        params={"needs_reorder": "true"},
        headers=h,
    )
    assert reorder_only.json()["total"] == 2

    deactivated = await client.patch(
        f"/api/v1/products/{product['id']}",
        json={"is_active": False},
        headers=h,
    )
    assert deactivated.status_code == 200, deactivated.text
    assert deactivated.json()["is_active"] is False

    active_only = await client.get("/api/v1/products", headers=h)
    assert active_only.json()["total"] == 1
    with_inactive = await client.get(
        "/api/v1/products", params={"include_inactive": "true"}, headers=h
    )
    assert with_inactive.json()["total"] == 2

    viewer = await make_member(reg["active_org_id"], "viewer")
    viewer_h = _h(viewer["token"])
    assert (
        await client.get("/api/v1/products", headers=viewer_h)
    ).status_code == 200
    assert (
        await client.get("/api/v1/inventory/stock-levels", headers=viewer_h)
    ).status_code == 200
    denied = await client.post(
        "/api/v1/products",
        json={"sku": "NOPE", "name": "Nope", "sale_price": "1.00"},
        headers=viewer_h,
    )
    assert denied.status_code == 403

    bad_date_range = await client.get(
        "/api/v1/inventory/movements",
        params={"since": _now(), "until": "2000-01-01T00:00:00Z"},
        headers=h,
    )
    assert bad_date_range.status_code == 422


async def test_opening_stock_proposal_posts_and_reconciles(
    client, register_user
) -> None:
    reg = await register_user()
    h = _h(reg["access_token"])
    product = await _create_product(client, h, cost="10.00")
    key = uuid.uuid4().hex
    effective_at = _now()

    created = await _create_opening_stock(
        client,
        h,
        product["id"],
        quantity="10",
        unit_cost="10.00",
        idempotency_key=key,
        effective_at=effective_at,
    )
    proposal = created["proposal"]
    assert created["created"] is True
    assert proposal["proposal_type"] == "opening_stock"
    assert proposal["status"] == "pending"
    assert _d(proposal["amount"]) == Decimal("100.00")
    assert proposal["payload"]["lines"][0]["quantity"] == "10"
    assert proposal["payload"]["lines"][0]["unit_cost"] == "10.00"

    replay = await _create_opening_stock(
        client,
        h,
        product["id"],
        quantity="10",
        unit_cost="10.00",
        idempotency_key=key,
        effective_at=effective_at,
    )
    assert replay["created"] is False
    assert replay["proposal"]["id"] == proposal["id"]

    posted = await _approve(client, h, proposal["id"])
    assert posted["status"] == "applied", posted["posting_error"]
    assert posted["journal_entry_id"] is not None

    level = await client.get(
        f"/api/v1/inventory/stock-levels/{product['id']}", headers=h
    )
    assert level.status_code == 200, level.text
    assert _d(level.json()["quantity_on_hand"]) == Decimal("10")
    assert level.json()["needs_reorder"] is False

    movements = await client.get(
        "/api/v1/inventory/movements",
        params={"product_id": product["id"]},
        headers=h,
    )
    assert movements.status_code == 200, movements.text
    assert movements.json()["total"] == 1
    movement = movements.json()["items"][0]
    assert movement["movement_type"] == "opening_balance"
    assert _d(movement["quantity_delta"]) == Decimal("10")
    assert _d(movement["unit_cost"]) == Decimal("10")
    assert movement["journal_entry_id"] == posted["journal_entry_id"]

    valuation = await client.get("/api/v1/inventory/valuation", headers=h)
    assert valuation.status_code == 200, valuation.text
    report = valuation.json()
    assert _d(report["total_book_value"]) == Decimal("100")
    assert _d(report["inventory_account_balance"]) == Decimal("100")
    assert report["is_reconciled"] is True
    assert report["rows"][0]["product_id"] == product["id"]

    detail = await client.get(
        f"/api/v1/inventory/opening-stock/{proposal['id']}", headers=h
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "applied"

    missing_product = await client.post(
        "/api/v1/inventory/opening-stock",
        json={
            "effective_at": _now(),
            "lines": [
                {
                    "product_id": str(uuid.uuid4()),
                    "quantity": "1",
                    "unit_cost": "1.00",
                }
            ],
        },
        headers=h,
    )
    assert missing_product.status_code == 422

    duplicate_lines = await client.post(
        "/api/v1/inventory/opening-stock",
        json={
            "effective_at": _now(),
            "lines": [
                {
                    "product_id": product["id"],
                    "quantity": "1",
                    "unit_cost": "1.00",
                },
                {
                    "product_id": product["id"],
                    "quantity": "2",
                    "unit_cost": "1.00",
                },
            ],
        },
        headers=h,
    )
    assert duplicate_lines.status_code == 422


async def test_stock_adjustments_in_and_out(client, register_user) -> None:
    reg = await register_user()
    h = _h(reg["access_token"])
    product = await _create_product(client, h, cost="10.00")
    opening = await _create_opening_stock(
        client, h, product["id"], quantity="10", unit_cost="10.00"
    )
    await _approve(client, h, opening["proposal"]["id"])

    insufficient = await client.post(
        "/api/v1/inventory/adjustments",
        json={
            "direction": "out",
            "effective_at": _now(),
            "reason": "Too much",
            "lines": [
                {
                    "product_id": product["id"],
                    "quantity": "11",
                    "unit_cost": "10.00",
                }
            ],
        },
        headers=h,
    )
    assert insufficient.status_code == 422, insufficient.text

    outbound = await _create_adjustment(
        client,
        h,
        product["id"],
        direction="out",
        quantity="3",
        unit_cost="10.00",
        reason="Damaged goods",
    )
    assert outbound["proposal"]["proposal_type"] == "stock_adjustment"
    assert _d(outbound["proposal"]["amount"]) == Decimal("30")
    out_posted = await _approve(client, h, outbound["proposal"]["id"])
    assert out_posted["status"] == "applied", out_posted["posting_error"]

    level = await client.get(
        f"/api/v1/inventory/stock-levels/{product['id']}", headers=h
    )
    assert _d(level.json()["quantity_on_hand"]) == Decimal("7")

    inbound = await _create_adjustment(
        client,
        h,
        product["id"],
        direction="in",
        quantity="2",
        unit_cost="10.00",
        reason="Found stock",
    )
    in_posted = await _approve(client, h, inbound["proposal"]["id"])
    assert in_posted["status"] == "applied", in_posted["posting_error"]

    level = await client.get(
        f"/api/v1/inventory/stock-levels/{product['id']}", headers=h
    )
    assert _d(level.json()["quantity_on_hand"]) == Decimal("9")

    movements = await client.get(
        "/api/v1/inventory/movements",
        params={"product_id": product["id"]},
        headers=h,
    )
    assert movements.status_code == 200, movements.text
    kinds = [item["movement_type"] for item in movements.json()["items"]]
    assert kinds.count("opening_balance") == 1
    assert kinds.count("adjustment_out") == 1
    assert kinds.count("adjustment_in") == 1

    outbound_movements = await client.get(
        "/api/v1/inventory/movements",
        params={"movement_type": "adjustment_out"},
        headers=h,
    )
    assert outbound_movements.status_code == 200, outbound_movements.text
    assert outbound_movements.json()["total"] == 1
    assert _d(
        outbound_movements.json()["items"][0]["quantity_delta"]
    ) == Decimal("-3")

    valuation = await client.get("/api/v1/inventory/valuation", headers=h)
    report = valuation.json()
    assert _d(report["total_book_value"]) == Decimal("90")
    assert _d(report["inventory_account_balance"]) == Decimal("90")
    assert report["is_reconciled"] is True

    adjustments = await client.get(
        "/api/v1/inventory/adjustments", headers=h
    )
    assert adjustments.status_code == 200, adjustments.text
    assert adjustments.json()["total"] == 2

    detail = await client.get(
        f"/api/v1/inventory/adjustments/{inbound['proposal']['id']}", headers=h
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "applied"

    wrong_type = await client.get(
        f"/api/v1/inventory/opening-stock/{inbound['proposal']['id']}", headers=h
    )
    assert wrong_type.status_code == 404


async def test_inventory_tenant_isolation(client, register_user) -> None:
    first = await register_user(org_name="Inventory Org A")
    first_h = _h(first["access_token"])
    product = await _create_product(client, first_h)
    opening = await _create_opening_stock(
        client, first_h, product["id"], quantity="2", unit_cost="5.00"
    )
    await _approve(client, first_h, opening["proposal"]["id"])

    second = await register_user(org_name="Inventory Org B")
    second_h = _h(second["access_token"])

    products = await client.get("/api/v1/products", headers=second_h)
    assert products.status_code == 200, products.text
    assert products.json()["total"] == 0

    detail = await client.get(
        f"/api/v1/products/{product['id']}", headers=second_h
    )
    assert detail.status_code == 404

    level = await client.get(
        f"/api/v1/inventory/stock-levels/{product['id']}", headers=second_h
    )
    assert level.status_code == 404

    movements = await client.get("/api/v1/inventory/movements", headers=second_h)
    assert movements.status_code == 200, movements.text
    assert movements.json()["total"] == 0

    valuation = await client.get("/api/v1/inventory/valuation", headers=second_h)
    assert valuation.status_code == 200, valuation.text
    assert valuation.json()["rows"] == []
    assert _d(valuation.json()["total_book_value"]) == Decimal("0")

    same_sku = await _create_product(
        client, second_h, sku=product["sku"], name="Other tenant item"
    )
    assert same_sku["id"] != product["id"]
