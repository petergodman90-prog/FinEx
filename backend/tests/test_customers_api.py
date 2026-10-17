"""Customer CRM API tests."""

from __future__ import annotations


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_customer_crud_dedup_search(client, register_user) -> None:
    reg = await register_user()
    h = _h(reg["access_token"])

    created = await client.post(
        "/api/v1/customers",
        json={
            "name": "Acme Traders",
            "email": "billing@acme.test",
            "phone": "+44 7911 123456",
            "company": "Acme Ltd",
        },
        headers=h,
    )
    assert created.status_code == 201, created.text
    customer = created.json()
    cid = customer["id"]
    assert customer["email"] == "billing@acme.test"
    # Phones are stored/returned in canonical (formatting-insensitive) form.
    assert customer["phone"] == "+447911123456"

    # Duplicate phone with different formatting → 409 + existing id.
    dup_phone = await client.post(
        "/api/v1/customers",
        json={"name": "Acme Copy", "phone": "+447911123456"},
        headers=h,
    )
    assert dup_phone.status_code == 409, dup_phone.text
    assert dup_phone.json()["details"]["existing_customer_id"] == cid

    # Duplicate email → 409.
    dup_email = await client.post(
        "/api/v1/customers",
        json={"name": "Acme Email", "email": "BILLING@acme.test"},
        headers=h,
    )
    assert dup_email.status_code == 409

    # Search by partial name.
    by_name = await client.get("/api/v1/customers", params={"q": "acm"}, headers=h)
    assert by_name.status_code == 200
    assert by_name.json()["total"] == 1

    # Search by phone (formatting-insensitive).
    by_phone = await client.get(
        "/api/v1/customers", params={"q": "+44 7911 123456"}, headers=h
    )
    assert by_phone.json()["total"] == 1

    # Update.
    patched = await client.patch(
        f"/api/v1/customers/{cid}", json={"name": "Acme Traders Ltd"}, headers=h
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "Acme Traders Ltd"

    # Soft delete, then 404 on read.
    deleted = await client.delete(f"/api/v1/customers/{cid}", headers=h)
    assert deleted.status_code == 204
    missing = await client.get(f"/api/v1/customers/{cid}", headers=h)
    assert missing.status_code == 404

    # A soft-deleted row must not block recreating the same phone.
    recreated = await client.post(
        "/api/v1/customers",
        json={"name": "Acme Again", "phone": "+44 7911 123456"},
        headers=h,
    )
    assert recreated.status_code == 201, recreated.text


async def test_customer_permissions(client, register_user, make_member) -> None:
    reg = await register_user()
    owner_h = _h(reg["access_token"])
    viewer = await make_member(reg["active_org_id"], "viewer")
    viewer_h = _h(viewer["token"])

    await client.post("/api/v1/customers", json={"name": "Visible Co"}, headers=owner_h)

    listed = await client.get("/api/v1/customers", headers=viewer_h)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    denied = await client.post(
        "/api/v1/customers", json={"name": "Nope"}, headers=viewer_h
    )
    assert denied.status_code == 403


async def test_customer_merge_moves_touchpoints(client, register_user) -> None:
    reg = await register_user()
    h = _h(reg["access_token"])

    a = (
        await client.post(
            "/api/v1/customers",
            json={"name": "Primary", "phone": "+15550100001"},
            headers=h,
        )
    ).json()
    b = (
        await client.post(
            "/api/v1/customers",
            json={"name": "Duplicate", "phone": "+15550100002"},
            headers=h,
        )
    ).json()

    await client.post(
        f"/api/v1/customers/{b['id']}/touchpoints",
        json={"kind": "call", "summary": "Called about invoice"},
        headers=h,
    )

    merged = await client.post(
        f"/api/v1/customers/{b['id']}/merge",
        json={"target_id": a["id"]},
        headers=h,
    )
    assert merged.status_code == 200, merged.text
    assert merged.json()["id"] == a["id"]

    # Source is a soft-deleted tombstone.
    assert (
        await client.get(f"/api/v1/customers/{b['id']}", headers=h)
    ).status_code == 404

    # Only the target remains listed.
    listed = await client.get("/api/v1/customers", headers=h)
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == a["id"]

    # Touchpoints moved to the target.
    touchpoints = await client.get(
        f"/api/v1/customers/{a['id']}/touchpoints", headers=h
    )
    assert touchpoints.status_code == 200
    assert touchpoints.json()["total"] == 1
