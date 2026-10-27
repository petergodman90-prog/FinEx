"""Restrict membership writes to org owners/admins.

Revision ID: 005_membership_rls
Revises: 004_posting_hardening
Create Date: 2026-10-27

The original memberships policy allowed a user to read *and write* any
membership row whose ``user_id`` matched theirs. A staff account could
therefore promote itself to owner through any future membership write path.
This revision keeps the read exception needed during login, but splits writes
into explicit owner/admin-only policies scoped to the active organisation.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

from app.core import rls

# revision identifiers, used by Alembic.
revision: str = "005_membership_rls"
down_revision: Union[str, Sequence[str], None] = "004_posting_hardening"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _exec(statement: str) -> None:
    op.get_bind().exec_driver_sql(statement)


def upgrade() -> None:
    for statement in rls.rls_statements("memberships"):
        _exec(statement)


def downgrade() -> None:
    for name in rls.membership_policy_names():
        _exec(f"DROP POLICY IF EXISTS {name} ON memberships")
    _exec("DROP POLICY IF EXISTS memberships_tenant_isolation ON memberships")
    expression = (
        "org_id = current_setting('app.current_org_id', true)::uuid"
        " OR user_id = current_setting('app.current_user_id', true)::uuid"
    )
    _exec(
        "CREATE POLICY memberships_tenant_isolation ON memberships "
        f"USING ({expression}) WITH CHECK ({expression})"
    )
