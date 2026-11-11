"""Lock open items during settlement allocation.

Revision ID: 009_financial_locking
Revises: 008_proposal_posted_at
Create Date: 2026-11-11

Executes ``db/migrations/0004_financial_locking.sql``. The function is
replaced in place so existing databases receive the lock without a data
rewrite. The downgrade deliberately leaves the safer function body installed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence, Union

from alembic import op

from migrations.sql_utils import split_sql_statements

# revision identifiers, used by Alembic.
revision: str = "009_financial_locking"
down_revision: Union[str, Sequence[str], None] = "008_proposal_posted_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

REPO_ROOT = Path(__file__).resolve().parents[3]
LOCKING_SQL = (
    REPO_ROOT / "db" / "migrations" / "0004_financial_locking.sql"
)


def upgrade() -> None:
    if not LOCKING_SQL.exists():
        raise RuntimeError(
            f"Financial locking SQL not found at {LOCKING_SQL}. "
            "Run Alembic from a full checkout of the FinEx repository."
        )
    sql = LOCKING_SQL.read_text(encoding="utf-8")
    for statement in split_sql_statements(sql):
        op.get_bind().exec_driver_sql(statement)


def downgrade() -> None:
    # Forward-only: restoring the unlocked function would re-open the
    # concurrent over-settlement race. No data is changed by this revision.
    pass
