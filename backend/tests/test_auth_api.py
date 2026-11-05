"""Auth API end-to-end tests (fakeredis + real Postgres)."""

from __future__ import annotations

import asyncio
import uuid

from app.core.config import get_settings
from app.core.cookies import REFRESH_COOKIE
from app.core.db import SessionLocal
from app.core.sessions import revoke_all_user_tokens
from app.services import auth_service


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_browser_register_hides_tokens(client, tracker) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "org_name": "Cookie Only",
            "full_name": "Cookie User",
            "email": f"browser-{uuid.uuid4().hex[:12]}@example.com",
            "password": "password123",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert "access_token" not in body
    assert "refresh_token" not in body
    assert client.cookies.get("finex_access")
    assert client.cookies.get("finex_refresh")
    tracker.add(body["active_org_id"])


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
    assert "access_token" not in login.json()
    assert "refresh_token" not in login.json()

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
    old_refresh = reg["refresh_token"]

    first = await client.post("/api/v1/auth/refresh")
    assert first.status_code == 200, first.text
    assert "refresh_token" not in first.json()
    new_refresh = client.cookies.get(REFRESH_COOKIE)
    assert new_refresh and new_refresh != old_refresh

    second = await client.post(
        "/api/v1/auth/token/refresh",
        json={"refresh_token": new_refresh},
    )
    assert second.status_code == 200, second.text
    newest_refresh = second.json()["refresh_token"]
    assert newest_refresh

    # The presented token was burned (rotation).
    replay = await client.post(
        "/api/v1/auth/token/refresh",
        json={"refresh_token": old_refresh},
    )
    assert replay.status_code == 401

    # Reuse detection kills the whole token family, including the token
    # issued by the legitimate rotation above.
    family = await client.post(
        "/api/v1/auth/token/refresh",
        json={"refresh_token": newest_refresh},
    )
    assert family.status_code == 401


async def test_concurrent_refresh_has_one_winner(client, register_user) -> None:
    reg = await register_user()
    refresh = reg["refresh_token"]

    responses = await asyncio.gather(
        client.post(
            "/api/v1/auth/token/refresh",
            json={"refresh_token": refresh},
        ),
        client.post(
            "/api/v1/auth/token/refresh",
            json={"refresh_token": refresh},
        ),
    )
    assert sorted(response.status_code for response in responses) == [200, 401]


async def test_login_rate_limit(client, register_user) -> None:
    reg = await register_user()

    for _ in range(5):
        failed = await client.post(
            "/api/v1/auth/login",
            json={"email": reg["user"]["email"], "password": "wrong-password"},
        )
        assert failed.status_code == 401

    blocked = await client.post(
        "/api/v1/auth/login",
        json={"email": reg["user"]["email"], "password": "wrong-password"},
    )
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) >= 1


async def test_api_token_login_is_explicit(client, register_user) -> None:
    reg = await register_user()
    token_response = await client.post(
        "/api/v1/auth/token",
        json={"email": reg["user"]["email"], "password": "password123"},
    )
    assert token_response.status_code == 200, token_response.text
    body = token_response.json()
    assert body["access_token"]
    assert body["refresh_token"]

    me = await client.get(
        "/api/v1/auth/me",
        headers=_h(body["access_token"]),
    )
    assert me.status_code == 200


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
    assert "access_token" not in body
    switched_token = client.cookies.get("finex_access")
    assert switched_token

    me = await client.get("/api/v1/auth/me", headers=_h(switched_token))
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


async def test_kill_switch_ttl_covers_refresh_lifetime(
    fake_redis,
    register_user,
) -> None:
    reg = await register_user()
    await revoke_all_user_tokens(reg["user"]["id"])
    ttl = await fake_redis.ttl(f"finex:revoked:user:{reg['user']['id']}")
    settings = get_settings()
    expected = (
        max(
            settings.ACCESS_TOKEN_EXPIRE_MINUTES,
            settings.REFRESH_TOKEN_EXPIRE_MINUTES,
        )
        * 60
        + 1
    )
    assert expected - 2 <= ttl <= expected


async def test_sign_in_works_after_kill_switch(client, register_user) -> None:
    reg = await register_user()
    await revoke_all_user_tokens(reg["user"]["id"])
    await asyncio.sleep(1.05)

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": reg["user"]["email"], "password": "password123"},
    )
    assert login.status_code == 200, login.text
    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text


async def test_auth_required(client) -> None:
    r = await client.get("/api/v1/auth/me")
    assert r.status_code in (401, 403)
