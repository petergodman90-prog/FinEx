"""Proposal API tests — idempotency, spend caps, expiry."""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select, text, update

from app.core.db import SessionLocal
from app.core.rls import bind_session_context
from app.models.audit_log import AuditLog
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


async def test_manager_cannot_approve_own_proposal(
    client, register_user, make_member
) -> None:
    reg = await register_user()
    owner_h = _h(reg["access_token"])
    manager = await make_member(reg["active_org_id"], "manager")
    manager_h = _h(manager["token"])

    created = await client.post(
        "/api/v1/proposals",
        json={
            "proposal_type": "expense",
            "title": "Manager's own expense",
            "amount": "100.00",
        },
        headers=manager_h,
    )
    assert created.status_code == 200, created.text
    proposal_id = created.json()["proposal"]["id"]

    blocked = await client.post(
        f"/api/v1/proposals/{proposal_id}/approve",
        json={},
        headers=manager_h,
    )
    assert blocked.status_code == 403
    assert "cannot approve proposals they created" in blocked.json()["detail"]

    pending = await client.get(
        f"/api/v1/proposals/{proposal_id}", headers=manager_h
    )
    assert pending.json()["status"] == "pending"

    # A different approver can still decide it.
    approved = await client.post(
        f"/api/v1/proposals/{proposal_id}/approve",
        json={},
        headers=owner_h,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert approved.json()["decided_by_id"] == reg["user"]["id"]


async def test_missing_amount_cannot_bypass_spend_cap(
    client, register_user, make_member
) -> None:
    reg = await register_user()
    manager = await make_member(reg["active_org_id"], "manager")

    created = await client.post(
        "/api/v1/proposals",
        json={
            "proposal_type": "expense",
            "title": "Unquantified factory order",
            "payload": {"amount": "999999999.00"},
        },
        headers=_h(reg["access_token"]),
    )
    assert created.status_code == 200, created.text
    proposal_id = created.json()["proposal"]["id"]

    blocked = await client.post(
        f"/api/v1/proposals/{proposal_id}/approve",
        json={},
        headers=_h(manager["token"]),
    )
    assert blocked.status_code == 403
    assert blocked.json()["details"]["amount"] is None

    owner_ok = await client.post(
        f"/api/v1/proposals/{proposal_id}/approve",
        json={},
        headers=_h(reg["access_token"]),
    )
    assert owner_ok.status_code == 200, owner_ok.text


async def test_concurrent_approval_has_one_decision(client, register_user) -> None:
    reg = await register_user()
    h = _h(reg["access_token"])
    org_id = reg["active_org_id"]
    user_id = reg["user"]["id"]
    created = await client.post(
        "/api/v1/proposals",
        json={
            "proposal_type": "expense",
            "title": "Race",
            "payload": _expense_payload(),
            "amount": "250.00",
            "currency": "NGN",
        },
        headers=h,
    )
    proposal_id = created.json()["proposal"]["id"]
    before = await _ledger_counts(org_id, user_id)

    responses = await asyncio.gather(
        client.post(
            f"/api/v1/proposals/{proposal_id}/approve",
            json={},
            headers=h,
        ),
        client.post(
            f"/api/v1/proposals/{proposal_id}/approve",
            json={},
            headers=h,
        ),
    )
    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(response for response in responses if response.status_code == 200)
    body = winner.json()
    assert body["status"] == "applied"
    assert body["journal_entry_id"] is not None
    assert body["posted_at"] is not None

    after = await _ledger_counts(org_id, user_id)
    assert after["journal_entries"] == before["journal_entries"] + 1
    assert after["journal_lines"] == before["journal_lines"] + 2
    assert after["posting_events"] == before["posting_events"] + 1
    assert after["stock_movements"] == before["stock_movements"]
    assert await _audit_actions(org_id, user_id, proposal_id) == [
        "proposal.created",
        "proposal.approved",
        "proposal.applied",
    ]


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

    retry = await client.post(
        f"/api/v1/proposals/{proposal_id}/retry-posting", headers=h
    )
    assert retry.status_code == 409


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


async def _ledger_counts(
    org_id: str | uuid.UUID, user_id: str | uuid.UUID
) -> dict[str, int]:
    org = org_id if isinstance(org_id, uuid.UUID) else uuid.UUID(org_id)
    user = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(user_id)
    async with SessionLocal() as session:
        async with session.begin():
            await bind_session_context(
                session,
                org_id=org,
                user_id=user,
                role="owner",
            )
            counts = {}
            for table in (
                "journal_entries",
                "journal_lines",
                "posting_events",
                "stock_movements",
            ):
                counts[table] = int(
                    await session.scalar(
                        text(
                            f"SELECT count(*) FROM ledger.{table} "
                            "WHERE org_id = :org"
                        ),
                        {"org": org},
                    )
                    or 0
                )
            return counts


async def _journal_totals(
    org_id: str | uuid.UUID,
    user_id: str | uuid.UUID,
    journal_entry_id: str | uuid.UUID,
) -> tuple[int, int, Decimal, Decimal]:
    org = org_id if isinstance(org_id, uuid.UUID) else uuid.UUID(org_id)
    user = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(user_id)
    entry = (
        journal_entry_id
        if isinstance(journal_entry_id, uuid.UUID)
        else uuid.UUID(journal_entry_id)
    )
    async with SessionLocal() as session:
        async with session.begin():
            await bind_session_context(
                session,
                org_id=org,
                user_id=user,
                role="owner",
            )
            params = {"org": org, "entry": entry}
            entry_count = await session.scalar(
                text(
                    "SELECT count(*) FROM ledger.journal_entries "
                    "WHERE org_id = :org AND id = :entry"
                ),
                params,
            )
            row = (
                await session.execute(
                    text(
                        """
                        SELECT count(*) AS line_count,
                               coalesce(sum(debit), 0) AS debit,
                               coalesce(sum(credit), 0) AS credit
                        FROM ledger.journal_lines
                        WHERE org_id = :org AND journal_entry_id = :entry
                        """
                    ),
                    params,
                )
            ).one()
            return (
                int(entry_count or 0),
                int(row.line_count),
                Decimal(row.debit),
                Decimal(row.credit),
            )


async def _audit_actions(
    org_id: str | uuid.UUID,
    user_id: str | uuid.UUID,
    proposal_id: str | uuid.UUID,
) -> list[str]:
    org = org_id if isinstance(org_id, uuid.UUID) else uuid.UUID(org_id)
    user = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(user_id)
    async with SessionLocal() as session:
        async with session.begin():
            await bind_session_context(
                session,
                org_id=org,
                user_id=user,
                role="owner",
            )
            rows = (
                await session.execute(
                    select(AuditLog.action).where(
                        AuditLog.org_id == org,
                        AuditLog.entity_id == str(proposal_id),
                    )
                )
            ).scalars().all()
            rank = {
                "proposal.created": 0,
                "proposal.approved": 1,
                "proposal.posting_failed": 2,
                "proposal.applied": 3,
            }
            return sorted(rows, key=lambda action: (rank.get(action, 99), action))


async def _audit_after_state(
    org_id: str | uuid.UUID,
    user_id: str | uuid.UUID,
    proposal_id: str | uuid.UUID,
    action: str,
) -> dict:
    org = org_id if isinstance(org_id, uuid.UUID) else uuid.UUID(org_id)
    user = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(user_id)
    async with SessionLocal() as session:
        async with session.begin():
            await bind_session_context(
                session,
                org_id=org,
                user_id=user,
                role="owner",
            )
            state = await session.scalar(
                select(AuditLog.after_state)
                .where(
                    AuditLog.org_id == org,
                    AuditLog.entity_id == str(proposal_id),
                    AuditLog.action == action,
                )
                .order_by(AuditLog.created_at.desc())
                .limit(1)
            )
            assert state is not None
            return state


async def test_approval_posts_and_marks_proposal_applied(client, register_user) -> None:
    reg = await register_user()
    h = _h(reg["access_token"])
    org_id = reg["active_org_id"]
    user_id = reg["user"]["id"]

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

    before = datetime.now(timezone.utc)
    approved = await client.post(
        f"/api/v1/proposals/{proposal_id}/approve", json={}, headers=h
    )
    after = datetime.now(timezone.utc)
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "applied"
    assert body["journal_entry_id"] is not None
    assert body["posting_error"] is None
    assert body["posted_at"] is not None
    posted_at = datetime.fromisoformat(body["posted_at"])
    assert posted_at.tzinfo is not None
    assert before <= posted_at <= after

    entry_count, line_count, debit, credit = await _journal_totals(
        org_id, user_id, body["journal_entry_id"]
    )
    assert entry_count == 1
    assert line_count == 2
    assert debit == credit == Decimal("250.00")

    assert await _audit_actions(org_id, user_id, proposal_id) == [
        "proposal.created",
        "proposal.approved",
        "proposal.applied",
    ]
    applied_audit = await _audit_after_state(
        org_id, user_id, proposal_id, "proposal.applied"
    )
    assert applied_audit["journal_entry_id"] == body["journal_entry_id"]
    assert datetime.fromisoformat(applied_audit["posted_at"]) == posted_at
    assert applied_audit["posting_error"] is None

    counts = await _ledger_counts(org_id, user_id)

    # Posting retries are idempotent and return the original journal entry.
    replayed = await client.post(
        f"/api/v1/proposals/{proposal_id}/retry-posting", headers=h
    )
    assert replayed.status_code == 200, replayed.text
    assert replayed.json()["journal_entry_id"] == body["journal_entry_id"]
    assert replayed.json()["posted_at"] == body["posted_at"]
    assert await _ledger_counts(org_id, user_id) == counts

    # The legacy /post path remains a compatibility alias.
    alias = await client.post(f"/api/v1/proposals/{proposal_id}/post", headers=h)
    assert alias.status_code == 200
    assert alias.json()["journal_entry_id"] == body["journal_entry_id"]

    applied_page = await client.get(
        "/api/v1/proposals", params={"status": "applied"}, headers=h
    )
    assert applied_page.status_code == 200
    assert applied_page.json()["total"] == 1
    approved_page = await client.get(
        "/api/v1/proposals", params={"status": "approved"}, headers=h
    )
    assert approved_page.status_code == 200
    assert approved_page.json()["total"] == 0


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
    counts_before = await _ledger_counts(org_id, user_id)

    approved = await client.post(
        f"/api/v1/proposals/{proposal_id}/approve", json={}, headers=h
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "approved"
    assert body["journal_entry_id"] is None
    assert body["posted_at"] is None
    assert body["posting_error"]["code"] == "closed_period"
    assert body["posting_error"]["retryable"] is True
    assert await _ledger_counts(org_id, user_id) == counts_before
    assert await _audit_actions(org_id, user_id, proposal_id) == [
        "proposal.created",
        "proposal.approved",
        "proposal.posting_failed",
    ]
    failed_audit = await _audit_after_state(
        org_id, user_id, proposal_id, "proposal.posting_failed"
    )
    assert failed_audit["journal_entry_id"] is None
    assert failed_audit["posted_at"] is None
    assert failed_audit["posting_error"]["code"] == "closed_period"

    async with SessionLocal() as session:
        await bind_session_context(
            session, org_id=org_id, user_id=user_id, role="owner"
        )
        await posting_service.reopen_accounting_period(
            session, org_id, start=start, end=end, user_id=user_id
        )
        await session.commit()

    retried = await client.post(
        f"/api/v1/proposals/{proposal_id}/retry-posting", headers=h
    )
    assert retried.status_code == 200, retried.text
    body = retried.json()
    assert body["status"] == "applied"
    assert body["journal_entry_id"] is not None
    assert body["posted_at"] is not None
    assert body["posting_error"] is None

    counts_after = await _ledger_counts(org_id, user_id)
    assert counts_after["journal_entries"] == counts_before["journal_entries"] + 1
    assert counts_after["journal_lines"] == counts_before["journal_lines"] + 2
    assert counts_after["posting_events"] == counts_before["posting_events"] + 1
    assert counts_after["stock_movements"] == counts_before["stock_movements"]
    assert await _audit_actions(org_id, user_id, proposal_id) == [
        "proposal.created",
        "proposal.approved",
        "proposal.posting_failed",
        "proposal.applied",
    ]

    replay = await client.post(
        f"/api/v1/proposals/{proposal_id}/retry-posting", headers=h
    )
    assert replay.status_code == 200
    assert replay.json()["journal_entry_id"] == body["journal_entry_id"]
    assert replay.json()["posted_at"] == body["posted_at"]
    assert await _ledger_counts(org_id, user_id) == counts_after


async def test_generic_proposal_endpoint_rejects_domain_types(
    client, register_user
) -> None:
    reg = await register_user()
    h = _h(reg["access_token"])

    for proposal_type in (
        "sale",
        "credit_note",
        "opening_stock",
        "stock_adjustment",
        "settlement",
    ):
        response = await client.post(
            "/api/v1/proposals",
            json={"proposal_type": proposal_type, "title": "Bypass attempt"},
            headers=h,
        )
        assert response.status_code == 422, response.text
        assert (
            response.json()["detail"]
            == "This proposal type must be created through its dedicated endpoint"
        )
        assert response.json()["details"]["proposal_type"] == proposal_type

    for proposal_type in ("manual", "expense", "purchase"):
        response = await client.post(
            "/api/v1/proposals",
            json={"proposal_type": proposal_type, "title": f"{proposal_type} note"},
            headers=h,
        )
        assert response.status_code == 200, response.text
        assert response.json()["proposal"]["proposal_type"] == proposal_type

    # Case is normalised before storage and idempotency hashing.
    response = await client.post(
        "/api/v1/proposals",
        json={"proposal_type": "Expense", "title": "Case-normalised expense"},
        headers=h,
    )
    assert response.status_code == 200, response.text
    assert response.json()["proposal"]["proposal_type"] == "expense"


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
    assert body["posted_at"] is None
    assert body["posting_error"] is None

    retry = await client.post(
        f"/api/v1/proposals/{proposal_id}/retry-posting", headers=h
    )
    assert retry.status_code == 409


async def test_unsupported_proposal_type_is_not_posted(client, register_user) -> None:
    reg = await register_user()
    h = _h(reg["access_token"])
    org_id = reg["active_org_id"]
    user_id = reg["user"]["id"]

    created = await client.post(
        "/api/v1/proposals",
        json={
            "proposal_type": "purchase",
            "title": "Not a financial event",
            "payload": {"unexpected": True},
            "amount": "10.00",
            "currency": "NGN",
        },
        headers=h,
    )
    assert created.status_code == 200, created.text
    proposal_id = created.json()["proposal"]["id"]
    counts_before = await _ledger_counts(org_id, user_id)

    approved = await client.post(
        f"/api/v1/proposals/{proposal_id}/approve", json={}, headers=h
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "approved"
    assert body["journal_entry_id"] is None
    assert body["posted_at"] is None
    assert body["posting_error"]["code"] == "unsupported_proposal_type"
    assert body["posting_error"]["retryable"] is False
    assert await _ledger_counts(org_id, user_id) == counts_before
    assert await _audit_actions(org_id, user_id, proposal_id) == [
        "proposal.created",
        "proposal.approved",
        "proposal.posting_failed",
    ]

    retry = await client.post(
        f"/api/v1/proposals/{proposal_id}/retry-posting", headers=h
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["status"] == "approved"
    assert retry.json()["posting_error"]["code"] == "unsupported_proposal_type"
    assert await _ledger_counts(org_id, user_id) == counts_before
    assert (
        await _audit_actions(org_id, user_id, proposal_id)
    ).count("proposal.posting_failed") == 2


async def test_cross_org_cannot_access_approve_or_retry(
    client, register_user
) -> None:
    reg_a = await register_user(org_name="Proposal Org A")
    reg_b = await register_user(org_name="Proposal Org B")
    h_a = _h(reg_a["access_token"])
    h_b = _h(reg_b["access_token"])

    created = await client.post(
        "/api/v1/proposals",
        json={
            "proposal_type": "expense",
            "title": "Org A only",
            "payload": _expense_payload(),
            "amount": "250.00",
            "currency": "NGN",
        },
        headers=h_a,
    )
    proposal_id = created.json()["proposal"]["id"]

    hidden = await client.get(f"/api/v1/proposals/{proposal_id}", headers=h_b)
    assert hidden.status_code == 404
    hidden_approve = await client.post(
        f"/api/v1/proposals/{proposal_id}/approve", json={}, headers=h_b
    )
    assert hidden_approve.status_code == 404
    hidden_retry = await client.post(
        f"/api/v1/proposals/{proposal_id}/retry-posting", headers=h_b
    )
    assert hidden_retry.status_code == 404
    other_page = await client.get("/api/v1/proposals", headers=h_b)
    assert other_page.json()["total"] == 0

    own = await client.get(f"/api/v1/proposals/{proposal_id}", headers=h_a)
    assert own.status_code == 200
    assert own.json()["status"] == "pending"


async def test_retry_requires_approve_permission_and_valid_state(
    client, register_user, make_member
) -> None:
    reg = await register_user()
    h = _h(reg["access_token"])
    staff = await make_member(reg["active_org_id"], "staff")

    created = await client.post(
        "/api/v1/proposals",
        json={"proposal_type": "manual", "title": "Retry state check"},
        headers=h,
    )
    proposal_id = created.json()["proposal"]["id"]

    forbidden = await client.post(
        f"/api/v1/proposals/{proposal_id}/retry-posting",
        headers=_h(staff["token"]),
    )
    assert forbidden.status_code == 403

    pending = await client.post(
        f"/api/v1/proposals/{proposal_id}/retry-posting", headers=h
    )
    assert pending.status_code == 409

    rejected = await client.post(
        f"/api/v1/proposals/{proposal_id}/reject", json={}, headers=h
    )
    assert rejected.status_code == 200
    retry_rejected = await client.post(
        f"/api/v1/proposals/{proposal_id}/retry-posting", headers=h
    )
    assert retry_rejected.status_code == 409
