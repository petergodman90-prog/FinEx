"""Dashboard aggregation API tests."""

from __future__ import annotations


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_dashboard_metrics(client, register_user) -> None:
    reg = await register_user()
    h = _h(reg["access_token"])

    customer = (
        await client.post("/api/v1/customers", json={"name": "Metric Co"}, headers=h)
    ).json()
    await client.post(
        f"/api/v1/customers/{customer['id']}/touchpoints",
        json={"kind": "note", "summary": "First contact"},
        headers=h,
    )
    await client.post(
        "/api/v1/proposals",
        json={"proposal_type": "expense", "title": "Something", "amount": "42.50"},
        headers=h,
    )

    response = await client.get("/api/v1/dashboard/metrics", headers=h)
    assert response.status_code == 200, response.text
    metrics = response.json()

    assert metrics["org_id"] == reg["active_org_id"]
    assert metrics["customers_total"] == 1
    assert metrics["customers_new_30d"] == 1
    assert metrics["touchpoints_30d"] == 1
    assert metrics["proposals_pending"] == 1
    assert metrics["proposals_pending_amount"] == "42.50"
    assert metrics["recent_touchpoints"][0]["customer_name"] == "Metric Co"
    assert "revenue_30d" in metrics["upstream_pending"]
