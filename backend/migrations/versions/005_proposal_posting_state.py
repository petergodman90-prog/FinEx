"""Approval-to-posting state on proposals.

Revision ID: 005_proposal_posting_state
Revises: 004_posting_hardening
Create Date: 2026-10-27

Approval is one transaction: the proposal is marked approved, the posting
service writes the ledger entry, and on success the proposal moves to
``applied`` with the journal-entry id.  A failure leaves the proposal
``approved`` with a structured ``posting_error`` so the approval can be retried.

There is deliberately no foreign key to ``ledger.journal_entries``: the ledger
domain remains independently usable and the public/ledger boundary is enforced
by the posting service instead.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "005_proposal_posting_state"
down_revision: Union[str, Sequence[str], None] = "004_posting_hardening"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "proposals",
        sa.Column(
            "journal_entry_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "proposals",
        sa.Column("posting_error", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("proposals", "posting_error")
    op.drop_column("proposals", "journal_entry_id")
