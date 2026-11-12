"""Financial report and dashboard ledger-metric API tests."""

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
            "sku": f"RPT-{uuid.uuid4().hex[:8]}",
            "name": "Report Widget",
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
        json={"name": f"Report Customer {uuid.uuid4().hex[:6]}"},
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
        json={"note": "approved in report test"},
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
            "title": "Report rent",
            "amount": "10.00",
            "currency": "NGN",
            "effective_at": "2026-01-20T10:00:00+00:00",
            "category": "rent",
            "vendor": "Report Vendor",
            "description": "Report test expense",
            "payment": {"method": "cash", "cash_account_code": "1000"},
            "idempotency_key": uuid.uuid4().hex,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_financial_reports_and_dashboard_metrics(
    client, register_user, make_member
) -> None:
    reg = await register_user()
    h = _h(reg["access_token"])

    product = await _create_product(client, h)
    customer = await _create_customer(client, h)
    opening = await _create_opening_stock(client, h, product["id"])
    await _approve(client, h, opening["proposal"]["id"])

    sale = await _create_sale(
        client,
        h,
        customer_id=customer["id"],
        product_id=product["id"],
    )
    posted_sale = await _approve(client, h, sale["sale"]["id"])
    assert posted_sale["status"] == "applied", posted_sale["posting_error"]

    expense = await _create_expense(client, h)
    posted_expense = await _approve(client, h, expense["expense"]["id"])
    assert posted_expense["status"] == "applied", posted_expense["posting_error"]

    pnl_response = await client.get(
        "/api/v1/reports/profit-and-loss",
        params={"start": REPORT_START, "end": REPORT_END},
        headers=h,
    )
    assert pnl_response.status_code == 200, pnl_response.text
    pnl = pnl_response.json()
    assert pnl["period_start"] == REPORT_START
    assert pnl["period_end"] == REPORT_END
    assert _d(pnl["revenue_total"]) == Decimal("51.00")
    assert _d(pnl["cogs_total"]) == Decimal("20.00")
    assert _d(pnl["gross_profit"]) == Decimal("31.00")
    assert _d(pnl["operating_expense_total"]) == Decimal("10.00")
    assert _d(pnl["net_profit"]) == Decimal("21.00")
    assert {line["account_code"]: _d(line["amount"]) for line in pnl["revenue_lines"]} == {
        "4000": Decimal("51.00")
    }
    assert {line["account_code"]: _d(line["amount"]) for line in pnl["cogs_lines"]} == {
        "5000": Decimal("20.00")
    }
    assert {
        line["account_code"]: _d(line["amount"])
        for line in pnl["operating_expense_lines"]
    } == {"6100": Decimal("10.00")}

    balance_response = await client.get(
        "/api/v1/reports/balance-sheet",
        params={"as_of": REPORT_END},
        headers=h,
    )
    assert balance_response.status_code == 200, balance_response.text
    balance = balance_response.json()
    assert balance["as_of"] == REPORT_END
    assert _d(balance["total_assets"]) == Decimal("121.00")
    assert _d(balance["total_liabilities"]) == Decimal("0")
    assert _d(balance["total_equity"]) == Decimal("100.00")
    assert _d(balance["current_earnings"]) == Decimal("21.00")
    assert _d(balance["total_liabilities_and_equity"]) == Decimal("121.00")
    assert balance["is_balanced"] is True

    trial_response = await client.get(
        "/api/v1/reports/trial-balance", headers=h
    )
    assert trial_response.status_code == 200, trial_response.text
    trial_rows = trial_response.json()["rows"]
    assert trial_rows
    assert sum((_d(row["debit_total"]) for row in trial_rows), Decimal("0")) == sum(
        (_d(row["credit_total"]) for row in trial_rows), Decimal("0")
    )
    assert sum(
        (_d(row["net_debit_balance"]) for row in trial_rows), Decimal("0")
    ) == Decimal("0")

    dashboard_response = await client.get(
        "/api/v1/dashboard/metrics", headers=h
    )
    assert dashboard_response.status_code == 200, dashboard_response.text
    metrics = dashboard_response.json()
    assert metrics["currency"] == "USD"
    assert _d(metrics["revenue_total"]) == Decimal("51.00")
    assert _d(metrics["cogs_total"]) == Decimal("20.00")
    assert _d(metrics["operating_expense_total"]) == Decimal("10.00")
    assert _d(metrics["expenses_total"]) == Decimal("30.00")
    assert _d(metrics["net_profit"]) == Decimal("21.00")
    assert metrics["orders_total"] == 1
    assert _d(metrics["receivables_outstanding"]) == Decimal("0")
    assert _d(metrics["payables_outstanding"]) == Decimal("0")

    viewer = await make_member(reg["active_org_id"], "viewer")
    viewer_report = await client.get(
        "/api/v1/reports/profit-and-loss",
        params={"start": REPORT_START, "end": REPORT_END},
        headers=_h(viewer["token"]),
    )
    assert viewer_report.status_code == 200, viewer_report.text

    invalid_period = await client.get(
        "/api/v1/reports/profit-and-loss",
        params={"start": "2026-02-01", "end": REPORT_START},
        headers=h,
    )
    assert invalid_period.status_code == 422
    assert invalid_period.json()["code"] == "validation_error"


async def test_reports_are_tenant_scoped(client, register_user) -> None:
    first = await register_user(org_name="Report Tenant A")
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
    await _approve(client, first_h, sale["sale"]["id"])

    second = await register_user(org_name="Report Tenant B")
    second_h = _h(second["access_token"])

    pnl_response = await client.get(
        "/api/v1/reports/profit-and-loss",
        params={"start": REPORT_START, "end": REPORT_END},
        headers=second_h,
    )
    assert pnl_response.status_code == 200, pnl_response.text
    assert _d(pnl_response.json()["revenue_total"]) == Decimal("0")

    balance_response = await client.get(
        "/api/v1/reports/balance-sheet",
        params={"as_of": REPORT_END},
        headers=second_h,
    )
    assert balance_response.status_code == 200, balance_response.text
    assert _d(balance_response.json()["total_assets"]) == Decimal("0")

    trial_response = await client.get(
        "/api/v1/reports/trial-balance", headers=second_h
    )
    assert trial_response.status_code == 200, trial_response.text
    assert trial_response.json()["rows"] == []
