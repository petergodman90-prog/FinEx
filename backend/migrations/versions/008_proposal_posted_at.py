"""Record when a proposal's ledger posting completed.

Revision ID: 008_proposal_posted_at
Revises: 007_merge_posting_staff
Create Date: 2026-11-03
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "008_proposal_posted_at"
down_revision: Union[str, Sequence[str], None] = "007_merge_posting_staff"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "proposals",
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("proposals", "posted_at")
