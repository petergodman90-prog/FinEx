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

# revision identifiers, used by Alembic.
revision: str = "005_membership_rls"
down_revision: Union[str, Sequence[str], None] = "004_posting_hardening"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_SELECT = (
    "org_id = current_setting('app.current_org_id', true)::uuid"
    " OR user_id = current_setting('app.current_user_id', true)::uuid"
)
_WRITE = (
    "org_id = current_setting('app.current_org_id', true)::uuid"
    " AND current_setting('app.current_role', true) IN ('owner', 'admin')"
)


def _exec(statement: str) -> None:
    op.get_bind().exec_driver_sql(statement)


def upgrade() -> None:
    # Frozen copy of the policies at revision 005. Migration 006 later replaces
    # them with lifecycle-aware policies after adding memberships.status.
    statements = [
        "ALTER TABLE memberships ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE memberships FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS memberships_tenant_isolation ON memberships",
        "DROP POLICY IF EXISTS memberships_select ON memberships",
        "DROP POLICY IF EXISTS memberships_insert ON memberships",
        "DROP POLICY IF EXISTS memberships_update ON memberships",
        "DROP POLICY IF EXISTS memberships_delete ON memberships",
        (
            "CREATE POLICY memberships_select ON memberships FOR SELECT "
            f"USING ({_SELECT})"
        ),
        (
            "CREATE POLICY memberships_insert ON memberships FOR INSERT "
            f"WITH CHECK ({_WRITE})"
        ),
        (
            "CREATE POLICY memberships_update ON memberships FOR UPDATE "
            f"USING ({_WRITE}) WITH CHECK ({_WRITE})"
        ),
        (
            "CREATE POLICY memberships_delete ON memberships FOR DELETE "
            f"USING ({_WRITE})"
        ),
    ]
    for statement in statements:
        _exec(statement)


def downgrade() -> None:
    for name in (
        "memberships_select",
        "memberships_insert",
        "memberships_update",
        "memberships_delete",
    ):
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
