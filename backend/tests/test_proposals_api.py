"""Proposal API tests — idempotency, spend caps, expiry."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import update

from app.core.db import SessionLocal
from app.core.rls import bind_session_context
from app.models.proposal import Proposal
from app.services import posting_service


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


def _expense_payload() -> dict:
    return {
        "amount": "250.00",
        "currency": "NGN",
        "effective_at": "2026-10-22T09:00:00+01:00",
        "expense_account_code": "6100",
        "category": "rent",
        "vendor": "Lagos Property Ltd",
        "description": "October rent",
        "payment": {"method": "cash", "cash_account_code": "1000"},
    }


async def test_approval_posts_and_marks_proposal_applied(client, register_user) -> None:
    reg = await register_user()
    h = _h(reg["access_token"])

    created = await client.post(
        "/api/v1/proposals",
        json={
            "proposal_type": "expense",
            "title": "October rent",
            "payload": _expense_payload(),
            "amount": "250.00",
            "currency": "NGN",
        },
        headers=h,
    )
    assert created.status_code == 200, created.text
    proposal_id = created.json()["proposal"]["id"]

    approved = await client.post(
        f"/api/v1/proposals/{proposal_id}/approve", json={}, headers=h
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "applied"
    assert body["journal_entry_id"] is not None
    assert body["posting_error"] is None

    # Posting retries are idempotent and return the original journal entry.
    replayed = await client.post(
        f"/api/v1/proposals/{proposal_id}/post", headers=h
    )
    assert replayed.status_code == 200, replayed.text
    assert replayed.json()["journal_entry_id"] == body["journal_entry_id"]


async def test_posting_failure_is_recorded_and_retry_posts(
    client, register_user
) -> None:
    reg = await register_user()
    h = _h(reg["access_token"])
    org_id = uuid.UUID(reg["active_org_id"])
    user_id = uuid.UUID(reg["user"]["id"])
    start, end = date(2026, 10, 1), date(2026, 10, 31)

    async with SessionLocal() as session:
        await bind_session_context(
            session, org_id=org_id, user_id=user_id, role="owner"
        )
        await posting_service.create_accounting_period(
            session, org_id, start=start, end=end, user_id=user_id
        )
        await posting_service.close_accounting_period(
            session, org_id, start=start, end=end, user_id=user_id
        )
        await session.commit()

    created = await client.post(
        "/api/v1/proposals",
        json={
            "proposal_type": "expense",
            "title": "October rent",
            "payload": _expense_payload(),
            "amount": "250.00",
            "currency": "NGN",
        },
        headers=h,
    )
    proposal_id = created.json()["proposal"]["id"]

    approved = await client.post(
        f"/api/v1/proposals/{proposal_id}/approve", json={}, headers=h
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "approved"
    assert body["journal_entry_id"] is None
    assert body["posting_error"]["code"] == "closed_period"
    assert body["posting_error"]["retryable"] is True

    async with SessionLocal() as session:
        await bind_session_context(
            session, org_id=org_id, user_id=user_id, role="owner"
        )
        await posting_service.reopen_accounting_period(
            session, org_id, start=start, end=end, user_id=user_id
        )
        await session.commit()

    retried = await client.post(
        f"/api/v1/proposals/{proposal_id}/post", headers=h
    )
    assert retried.status_code == 200, retried.text
    body = retried.json()
    assert body["status"] == "applied"
    assert body["journal_entry_id"] is not None
    assert body["posting_error"] is None

    replay = await client.post(f"/api/v1/proposals/{proposal_id}/post", headers=h)
    assert replay.json()["journal_entry_id"] == body["journal_entry_id"]


async def test_manual_proposal_is_never_posted(client, register_user) -> None:
    reg = await register_user()
    h = _h(reg["access_token"])

    created = await client.post(
        "/api/v1/proposals",
        json={"proposal_type": "manual", "title": "Note for the owner"},
        headers=h,
    )
    proposal_id = created.json()["proposal"]["id"]

    approved = await client.post(
        f"/api/v1/proposals/{proposal_id}/approve", json={}, headers=h
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "approved"
    assert body["journal_entry_id"] is None
    assert body["posting_error"] is None

    retry = await client.post(f"/api/v1/proposals/{proposal_id}/post", headers=h)
    assert retry.status_code == 409
