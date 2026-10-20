"""Auth API end-to-end tests (fakeredis + real Postgres)."""

from __future__ import annotations

import uuid

from app.core.db import SessionLocal
from app.core.sessions import revoke_all_user_tokens
from app.services import auth_service


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_register_login_me_logout(client, register_user) -> None:
    reg = await register_user(full_name="Alice Founder")
    assert reg["role"] == "owner"
    token = reg["access_token"]

    me = await client.get("/api/v1/auth/me", headers=_h(token))
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["user"]["full_name"] == "Alice Founder"
    assert len(body["memberships"]) == 1
    assert body["memberships"][0]["role"] == "owner"

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": reg["user"]["email"], "password": "password123"},
    )
    assert login.status_code == 200, login.text
    assert login.json()["active_org_id"] == reg["active_org_id"]

    bad = await client.post(
        "/api/v1/auth/login",
        json={"email": reg["user"]["email"], "password": "not-the-password"},
    )
    assert bad.status_code == 401

    logout = await client.post("/api/v1/auth/logout", headers=_h(token), json={})
    assert logout.status_code == 204

    revoked = await client.get("/api/v1/auth/me", headers=_h(token))
    assert revoked.status_code == 401

    # The session is dead, but the account still works.
    relogin = await client.post(
        "/api/v1/auth/login",
        json={"email": reg["user"]["email"], "password": "password123"},
    )
    assert relogin.status_code == 200


async def test_refresh_rotation(client, register_user) -> None:
    reg = await register_user()
    refresh = reg["refresh_token"]

    first = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert first.status_code == 200, first.text
    new_refresh = first.json()["refresh_token"]
    assert new_refresh and new_refresh != refresh

    # The presented token was burned (rotation).
    replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert replay.status_code == 401

    second = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": new_refresh}
    )
    assert second.status_code == 200


async def test_switch_org(client, register_user, tracker) -> None:
    reg = await register_user()
    async with SessionLocal() as session:
        org2, _membership = await auth_service.create_org(
            session, user_id=uuid.UUID(reg["user"]["id"]), org_name="Second Org"
        )
        await session.commit()
    tracker.add(str(org2.id))

    switch = await client.post(
        "/api/v1/auth/switch-org",
        json={"org_id": str(org2.id)},
        headers=_h(reg["access_token"]),
    )
    assert switch.status_code == 200, switch.text
    body = switch.json()
    assert body["active_org_id"] == str(org2.id)
    assert body["role"] == "owner"

    me = await client.get("/api/v1/auth/me", headers=_h(body["access_token"]))
    assert me.status_code == 200
    assert me.json()["active_org_id"] == str(org2.id)
    assert len(me.json()["memberships"]) == 2

    # Selecting an org the user is not a member of is forbidden.
    denied = await client.post(
        "/api/v1/auth/switch-org",
        json={"org_id": str(uuid.uuid4())},
        headers=_h(reg["access_token"]),
    )
    assert denied.status_code == 403


async def test_login_org_selection(client, register_user, tracker) -> None:
    reg = await register_user()
    async with SessionLocal() as session:
        org2, _ = await auth_service.create_org(
            session, user_id=uuid.UUID(reg["user"]["id"]), org_name="Pick Me"
        )
        await session.commit()
    tracker.add(str(org2.id))

    login = await client.post(
        "/api/v1/auth/login",
        json={
            "email": reg["user"]["email"],
            "password": "password123",
            "org_id": str(org2.id),
        },
    )
    assert login.status_code == 200, login.text
    assert login.json()["active_org_id"] == str(org2.id)


async def test_kill_switch_revokes_all_sessions(client, register_user) -> None:
    reg = await register_user()
    await revoke_all_user_tokens(reg["user"]["id"])
    me = await client.get("/api/v1/auth/me", headers=_h(reg["access_token"]))
    assert me.status_code == 401


async def test_auth_required(client) -> None:
    r = await client.get("/api/v1/auth/me")
    assert r.status_code in (401, 403)
