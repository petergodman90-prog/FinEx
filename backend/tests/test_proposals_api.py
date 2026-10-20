"""Proposal API tests — idempotency, spend caps, expiry."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from app.core.db import SessionLocal
from app.core.rls import bind_session_context
from app.models.proposal import Proposal


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_proposal_idempotency_and_decisions(client, register_user) -> None:
    reg = await register_user()
    h = _h(reg["access_token"])
    body = {
        "proposal_type": "expense",
        "title": "Buy a printer",
        "payload": {"item": "printer", "qty": 1},
        "amount": "500.00",
    }

    first = await client.post("/api/v1/proposals", json=body, headers=h)
    assert first.status_code == 200, first.text
    assert first.json()["created"] is True
    proposal_id = first.json()["proposal"]["id"]

    # Identical replay → same row, created=False.
    replay = await client.post("/api/v1/proposals", json=body, headers=h)
    assert replay.status_code == 200
    assert replay.json()["created"] is False
    assert replay.json()["proposal"]["id"] == proposal_id

    # Different content → a genuinely new proposal.
    other = await client.post(
        "/api/v1/proposals", json={**body, "amount": "501.00"}, headers=h
    )
    assert other.json()["created"] is True
    assert other.json()["proposal"]["id"] != proposal_id

    # Approve, then a second decision is a conflict.
    approved = await client.post(
        f"/api/v1/proposals/{proposal_id}/approve", json={"note": "ok"}, headers=h
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert approved.json()["decided_by_id"] == reg["user"]["id"]

    again = await client.post(
        f"/api/v1/proposals/{proposal_id}/approve", json={}, headers=h
    )
    assert again.status_code == 409

    # Reject the other one.
    rejected = await client.post(
        f"/api/v1/proposals/{other.json()['proposal']['id']}/reject",
        json={"note": "too expensive"},
        headers=h,
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"

    listed = await client.get(
        "/api/v1/proposals", params={"status": "approved"}, headers=h
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 1


async def test_proposal_spend_caps_and_permissions(
    client, register_user, make_member
) -> None:
    reg = await register_user()
    owner_h = _h(reg["access_token"])

    staff = await make_member(reg["active_org_id"], "staff")
    manager = await make_member(reg["active_org_id"], "manager")
    viewer = await make_member(reg["active_org_id"], "viewer")

    # Staff can create proposals but not approve them.
    created = await client.post(
        "/api/v1/proposals",
        json={"proposal_type": "expense", "title": "Laptop", "amount": "2500.00"},
        headers=_h(staff["token"]),
    )
    assert created.status_code == 200, created.text
    proposal_id = created.json()["proposal"]["id"]

    staff_approve = await client.post(
        f"/api/v1/proposals/{proposal_id}/approve",
        json={},
        headers=_h(staff["token"]),
    )
    assert staff_approve.status_code == 403

    # Viewer can neither read nor create.
    assert (
        await client.get("/api/v1/proposals", headers=_h(viewer["token"]))
    ).status_code == 200  # read allowed
    assert (
        await client.post(
            "/api/v1/proposals",
            json={"proposal_type": "expense", "title": "x"},
            headers=_h(viewer["token"]),
        )
    ).status_code == 403

    # Manager approves within cap.
    ok = await client.post(
        f"/api/v1/proposals/{proposal_id}/approve",
        json={},
        headers=_h(manager["token"]),
    )
    assert ok.status_code == 200, ok.text

    # Manager over their spend cap → 403 with details.
    big = await client.post(
        "/api/v1/proposals",
        json={"proposal_type": "expense", "title": "Factory", "amount": "500000.00"},
        headers=owner_h,
    )
    big_id = big.json()["proposal"]["id"]
    over_cap = await client.post(
        f"/api/v1/proposals/{big_id}/approve", json={}, headers=_h(manager["token"])
    )
    assert over_cap.status_code == 403
    assert over_cap.json()["details"]["spend_cap"] == "100000"

    # Owner has no cap.
    owner_ok = await client.post(
        f"/api/v1/proposals/{big_id}/approve", json={}, headers=owner_h
    )
    assert owner_ok.status_code == 200


async def test_proposal_expiry_flow(client, register_user) -> None:
    reg = await register_user()
    h = _h(reg["access_token"])

    created = await client.post(
        "/api/v1/proposals",
        json={"proposal_type": "expense", "title": "Expiring", "amount": "10.00"},
        headers=h,
    )
    proposal_id = uuid.UUID(created.json()["proposal"]["id"])

    # Force-expire directly in the DB.
    async with SessionLocal() as session:
        await bind_session_context(
            session,
            org_id=reg["active_org_id"],
            user_id=reg["user"]["id"],
            role="owner",
        )
        await session.execute(
            update(Proposal)
            .where(Proposal.id == proposal_id)
            .values(expires_at=datetime.now(timezone.utc) - timedelta(hours=1))
        )
        await session.commit()

    # Approval is refused once expired.
    approve = await client.post(
        f"/api/v1/proposals/{proposal_id}/approve", json={}, headers=h
    )
    assert approve.status_code == 409
    assert "expired" in approve.json()["detail"].lower()

    # Sweep marks it expired.
    sweep = await client.post("/api/v1/proposals/expire", headers=h)
    assert sweep.status_code == 200, sweep.text
    assert str(proposal_id) in sweep.json()["expired"]

    detail = await client.get(f"/api/v1/proposals/{proposal_id}", headers=h)
    assert detail.json()["status"] == "expired"
