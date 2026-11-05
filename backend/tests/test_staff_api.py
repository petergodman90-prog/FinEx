"""Staff directory, invitation, and staff-RBAC tests."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text, update
from sqlalchemy.exc import SQLAlchemyError

from app.core.db import SessionLocal
from app.core.rls import APP_DB_ROLE, bind_session_context
from app.models.membership import Membership


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _directory(client, token: str) -> dict:
    response = await client.get("/api/v1/staff", params={"limit": 100}, headers=_h(token))
    assert response.status_code == 200, response.text
    return response.json()


async def test_staff_invite_accept_suspend_and_dismiss(client, register_user) -> None:
    owner = await register_user(org_name="Staff Org A", full_name="Owner A")
    email = f"invitee-{uuid.uuid4().hex[:10]}@example.com"
    invitee = await register_user(
        org_name="Invitee Org", full_name="Invitee User", email=email
    )

    department = await client.post(
        "/api/v1/staff/departments",
        json={"name": "Operations", "description": "Runs the shop"},
        headers=_h(owner["access_token"]),
    )
    assert department.status_code == 201, department.text
    department_id = department.json()["id"]

    role = await client.post(
        "/api/v1/staff/roles",
        json={"name": "Shift Lead", "description": "Owns a shift"},
        headers=_h(owner["access_token"]),
    )
    assert role.status_code == 201, role.text

    owner_membership = next(
        item for item in (await _directory(client, owner["access_token"]))["items"]
        if item["user_id"] == owner["user"]["id"]
    )

    invited = await client.post(
        "/api/v1/staff",
        json={
            "email": email,
            "role": "manager",
            "job_title": "Shift Lead",
            "department_id": department_id,
            "reports_to_id": owner_membership["id"],
        },
        headers=_h(owner["access_token"]),
    )
    assert invited.status_code == 201, invited.text
    invitation = invited.json()
    assert invitation["status"] == "pending"
    assert invitation["full_name"] == "Invitee User"
    assert invitation["department_name"] == "Operations"
    assert invitation["job_title"] == "Shift Lead"

    directory = await _directory(client, owner["access_token"])
    assert directory["summary"] == {
        "total": 2,
        "active": 1,
        "pending": 1,
        "suspended": 0,
    }

    # Directory search covers name, job title, role (an enum cast in SQL), and
    # exact email. These all failed separately from the lifecycle flow above.
    for term, expected_email in (
        ("Shift", email),
        ("manager", email),
        (email, email),
    ):
        search = await client.get(
            "/api/v1/staff",
            params={"q": term, "limit": 100},
            headers=_h(owner["access_token"]),
        )
        assert search.status_code == 200, search.text
        assert any(
            item["email"] == expected_email for item in search.json()["items"]
        ), search.text

    # A pending invitation grants no access to the workspace.
    switch = await client.post(
        "/api/v1/auth/switch-org",
        json={"org_id": owner["active_org_id"]},
        headers=_h(invitee["access_token"]),
    )
    assert switch.status_code == 403

    pending = await client.get(
        "/api/v1/auth/invitations", headers=_h(invitee["access_token"])
    )
    assert pending.status_code == 200, pending.text
    assert pending.json()[0]["membership_id"] == invitation["id"]
    assert pending.json()[0]["org_name"] == "Staff Org A"

    accepted = await client.post(
        f"/api/v1/auth/invitations/{invitation['id']}/accept",
        headers=_h(invitee["access_token"]),
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["accepted"] is True

    me = await client.get("/api/v1/auth/me", headers=_h(invitee["access_token"]))
    assert me.status_code == 200
    assert {m["org_id"] for m in me.json()["memberships"]} == {
        invitee["active_org_id"],
        owner["active_org_id"],
    }

    switch = await client.post(
        "/api/v1/auth/switch-org",
        json={"org_id": owner["active_org_id"]},
        headers=_h(invitee["access_token"]),
    )
    assert switch.status_code == 200, switch.text
    # Browser responses keep tokens in cookies; use the owner's view for the
    # remaining assertions, which is the staff-management path under test.

    directory = await _directory(client, owner["access_token"])
    assert directory["summary"]["active"] == 2
    assert directory["summary"]["pending"] == 0

    suspended = await client.patch(
        f"/api/v1/staff/{invitation['id']}",
        json={"status": "suspended"},
        headers=_h(owner["access_token"]),
    )
    assert suspended.status_code == 200, suspended.text
    assert suspended.json()["status"] == "suspended"

    # The suspended member's old access token is revoked immediately.
    revoked = await client.get("/api/v1/auth/me", headers=_h(invitee["access_token"]))
    assert revoked.status_code == 401

    restored = await client.patch(
        f"/api/v1/staff/{invitation['id']}",
        json={"status": "active", "role": "staff"},
        headers=_h(owner["access_token"]),
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["status"] == "active"
    assert restored.json()["role"] == "staff"

    dismissed = await client.delete(
        f"/api/v1/staff/{invitation['id']}",
        headers=_h(owner["access_token"]),
    )
    assert dismissed.status_code == 204
    directory = await _directory(client, owner["access_token"])
    assert directory["summary"] == {
        "total": 1,
        "active": 1,
        "pending": 0,
        "suspended": 0,
    }


async def test_staff_permissions_and_self_protection(
    client, register_user, make_member
) -> None:
    owner = await register_user(org_name="Staff Permissions")
    manager = await make_member(owner["active_org_id"], "manager")
    admin = await make_member(owner["active_org_id"], "admin")
    staff = await make_member(owner["active_org_id"], "staff")

    directory = await _directory(client, owner["access_token"])
    by_user = {item["user_id"]: item for item in directory["items"]}
    owner_row = by_user[owner["user"]["id"]]
    admin_row = by_user[admin["user_id"]]
    staff_row = by_user[staff["user_id"]]

    # Managers may read, but not invite or change access.
    assert (
        await client.get(
            "/api/v1/staff", headers=_h(manager["token"])
        )
    ).status_code == 200
    assert (
        await client.post(
            "/api/v1/staff",
            json={"email": "nobody@example.com"},
            headers=_h(manager["token"]),
        )
    ).status_code == 403

    # Admins cannot modify owners or other admins.
    assert (
        await client.patch(
            f"/api/v1/staff/{owner_row['id']}",
            json={"status": "suspended"},
            headers=_h(admin["token"]),
        )
    ).status_code == 403
    assert (
        await client.patch(
            f"/api/v1/staff/{admin_row['id']}",
            json={"status": "suspended"},
            headers=_h(admin["token"]),
        )
    ).status_code == 403

    # Admins cannot promote a member to their own role or above.
    promoted = await client.patch(
        f"/api/v1/staff/{staff_row['id']}",
        json={"role": "admin"},
        headers=_h(admin["token"]),
    )
    assert promoted.status_code == 403

    # But an admin can promote staff to manager.
    manager_promotion = await client.patch(
        f"/api/v1/staff/{staff_row['id']}",
        json={"role": "manager"},
        headers=_h(admin["token"]),
    )
    assert manager_promotion.status_code == 200, manager_promotion.text

    # Nobody can suspend or dismiss themselves.
    assert (
        await client.patch(
            f"/api/v1/staff/{owner_row['id']}",
            json={"status": "suspended"},
            headers=_h(owner["access_token"]),
        )
    ).status_code == 403
    assert (
        await client.delete(
            f"/api/v1/staff/{owner_row['id']}",
            headers=_h(owner["access_token"]),
        )
    ).status_code == 403

    # A pending invitee cannot be activated by the inviter.
    email = f"pending-{uuid.uuid4().hex[:8]}@example.com"
    await register_user(org_name="Pending User Org", email=email)
    invited = await client.post(
        "/api/v1/staff",
        json={"email": email, "role": "staff"},
        headers=_h(owner["access_token"]),
    )
    assert invited.status_code == 201, invited.text
    pending_id = invited.json()["id"]
    assert (
        await client.patch(
            f"/api/v1/staff/{pending_id}",
            json={"status": "active"},
            headers=_h(owner["access_token"]),
        )
    ).status_code == 409


async def test_departments_roles_and_cycle_guards(
    client, register_user, make_member
) -> None:
    owner = await register_user(org_name="Staff Structure", full_name="Owner A")
    member = await make_member(owner["active_org_id"], "staff")
    second = await make_member(owner["active_org_id"], "staff")

    dept = await client.post(
        "/api/v1/staff/departments",
        json={"name": "Finance"},
        headers=_h(owner["access_token"]),
    )
    assert dept.status_code == 201
    dept_id = dept.json()["id"]
    assert (
        await client.post(
            "/api/v1/staff/departments",
            json={"name": " finance "},
            headers=_h(owner["access_token"]),
        )
    ).status_code == 409

    custom = await client.post(
        "/api/v1/staff/roles",
        json={"name": "Bookkeeper"},
        headers=_h(owner["access_token"]),
    )
    assert custom.status_code == 201
    custom_id = custom.json()["id"]
    assert (
        await client.post(
            "/api/v1/staff/roles",
            json={"name": "cashier"},
            headers=_h(owner["access_token"]),
        )
    ).status_code == 409

    directory = await _directory(client, owner["access_token"])
    owner_row = next(
        item for item in directory["items"]
        if item["user_id"] == owner["user"]["id"]
    )
    member_row = next(
        item for item in directory["items"]
        if item["user_id"] == member["user_id"]
    )
    second_row = next(
        item for item in directory["items"]
        if item["user_id"] == second["user_id"]
    )

    updated = await client.patch(
        f"/api/v1/staff/{member_row['id']}",
        json={
            "job_title": "Bookkeeper",
            "department_id": dept_id,
            "reports_to_id": owner_row["id"],
        },
        headers=_h(owner["access_token"]),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["department_name"] == "Finance"
    assert updated.json()["reports_to_name"] == "Owner A"

    # Establish member -> second, then reject the reverse link as a cycle.
    first_link = await client.patch(
        f"/api/v1/staff/{member_row['id']}",
        json={"reports_to_id": second_row["id"]},
        headers=_h(owner["access_token"]),
    )
    assert first_link.status_code == 200, first_link.text
    cycle = await client.patch(
        f"/api/v1/staff/{second_row['id']}",
        json={"reports_to_id": member_row["id"]},
        headers=_h(owner["access_token"]),
    )
    assert cycle.status_code == 422

    # Self-management is refused by the service before any cycle check.
    self_edit = await client.patch(
        f"/api/v1/staff/{owner_row['id']}",
        json={"reports_to_id": member_row["id"]},
        headers=_h(owner["access_token"]),
    )
    assert self_edit.status_code == 403

    # Deleting an assigned department/role is blocked.
    assert (
        await client.delete(
            f"/api/v1/staff/departments/{dept_id}",
            headers=_h(owner["access_token"]),
        )
    ).status_code == 409
    assert (
        await client.delete(
            f"/api/v1/staff/roles/{custom_id}",
            headers=_h(owner["access_token"]),
        )
    ).status_code == 409

    # Renaming an assigned role is blocked too.
    assert (
        await client.patch(
            f"/api/v1/staff/roles/{custom_id}",
            json={"name": "Senior Bookkeeper"},
            headers=_h(owner["access_token"]),
        )
    ).status_code == 409

    # After dismissing the member the structure can be cleaned up.
    assert (
        await client.delete(
            f"/api/v1/staff/{member_row['id']}",
            headers=_h(owner["access_token"]),
        )
    ).status_code == 204
    assert (
        await client.delete(
            f"/api/v1/staff/roles/{custom_id}",
            headers=_h(owner["access_token"]),
        )
    ).status_code == 204
    assert (
        await client.delete(
            f"/api/v1/staff/departments/{dept_id}",
            headers=_h(owner["access_token"]),
        )
    ).status_code == 204


async def test_self_accept_cannot_escalate_role(client, register_user) -> None:
    owner = await register_user(org_name="Escalation Guard")
    email = f"escalate-{uuid.uuid4().hex[:8]}@example.com"
    invitee = await register_user(org_name="Escalation User Org", email=email)

    invited = await client.post(
        "/api/v1/staff",
        json={"email": email, "role": "viewer"},
        headers=_h(owner["access_token"]),
    )
    assert invited.status_code == 201, invited.text
    membership_id = uuid.UUID(invited.json()["id"])

    async with SessionLocal() as session:
        await session.execute(text(f"SET ROLE {APP_DB_ROLE}"))
        try:
            await bind_session_context(
                session, user_id=uuid.UUID(invitee["user"]["id"])
            )
            with pytest.raises(SQLAlchemyError):
                await session.execute(
                    update(Membership)
                    .where(Membership.id == membership_id)
                    .values(role="owner", status="active")
                )
                await session.flush()
        finally:
            await session.rollback()
            await session.execute(text("RESET ROLE"))

    # Even the inviter cannot activate a pending invitation directly, and
    # the database enforces that independently of the service layer.
    async with SessionLocal() as session:
        await session.execute(text(f"SET ROLE {APP_DB_ROLE}"))
        try:
            await bind_session_context(
                session,
                org_id=uuid.UUID(str(owner["active_org_id"])),
                user_id=uuid.UUID(owner["user"]["id"]),
                role="owner",
            )
            with pytest.raises(SQLAlchemyError):
                await session.execute(
                    update(Membership)
                    .where(Membership.id == membership_id)
                    .values(status="active")
                )
                await session.flush()
        finally:
            await session.rollback()
            await session.execute(text("RESET ROLE"))
