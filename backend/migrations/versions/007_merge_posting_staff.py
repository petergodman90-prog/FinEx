"""Merge proposal-posting and staff-membership branches.

Revision ID: 007_merge_posting_staff
Revises: d04fd13a830d, 006_staff_membership
Create Date: 2026-11-03
"""

from __future__ import annotations

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "007_merge_posting_staff"
down_revision: Union[str, Sequence[str], None] = (
    "d04fd13a830d",
    "006_staff_membership",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
