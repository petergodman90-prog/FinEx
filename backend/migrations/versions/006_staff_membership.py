"""Staff directory, departments, and pending membership invitations.

Revision ID: 006_staff_membership
Revises: 005_membership_rls
Create Date: 2026-10-27

Adds the tenant-scoped metadata needed by the staff screen while keeping
access control on the existing membership role:

* ``departments`` and ``staff_roles`` are RLS-protected tenant tables;
* membership rows gain a lifecycle status (active/pending/suspended), a
  display-only job title, a department, and a manager link;
* pending invitations may only be activated/deleted by the invited user;
  a trigger prevents that self-service update from changing role/org/links;
* suspension/dismissal revokes the member's tokens in the service layer.

Global user identity (name/email) is intentionally not copied or made
editable here. A user may belong to several organisations; one tenant must
not rewrite another tenant's display identity.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.core import rls

# revision identifiers, used by Alembic.
revision: str = "006_staff_membership"
down_revision: Union[str, Sequence[str], None] = "005_membership_rls"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

APP_ROLE = rls.APP_DB_ROLE

MEMBERSHIP_STATUS = postgresql.ENUM(
    "active",
    "pending",
    "suspended",
    name="membership_status",
    create_type=False,
)


def _exec(statement: str) -> None:
    op.get_bind().exec_driver_sql(statement)


def _upgrade_memberships() -> None:
    MEMBERSHIP_STATUS.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "memberships",
        sa.Column(
            "status",
            MEMBERSHIP_STATUS,
            nullable=False,
            server_default=sa.text("'active'"),
        ),
    )
    op.add_column(
        "memberships", sa.Column("job_title", sa.String(80), nullable=True)
    )
    op.add_column(
        "memberships",
        sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "memberships",
        sa.Column("reports_to_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_memberships_department_id_departments",
        "memberships",
        "departments",
        ["department_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_memberships_reports_to_id_memberships",
        "memberships",
        "memberships",
        ["reports_to_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_memberships_status", "memberships", ["status"])
    op.create_index(
        "ix_memberships_department_id", "memberships", ["department_id"]
    )
    op.create_index(
        "ix_memberships_reports_to_id", "memberships", ["reports_to_id"]
    )
    op.create_index(
        "ix_memberships_org_status", "memberships", ["org_id", "status"]
    )
    op.create_check_constraint(
        "ck_memberships_reports_to_not_self",
        "memberships",
        "reports_to_id IS NULL OR reports_to_id <> id",
    )


def _create_staff_tables() -> None:
    op.create_table(
        "departments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("description", sa.String(240), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("org_id", "name", name="uq_departments_org_name"),
    )
    op.create_index("ix_departments_org_id", "departments", ["org_id"])
    op.create_index(
        "uq_departments_org_name_ci",
        "departments",
        ["org_id", sa.text("lower(name)")],
        unique=True,
    )

    op.create_table(
        "staff_roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("description", sa.String(240), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("org_id", "name", name="uq_staff_roles_org_name"),
    )
    op.create_index("ix_staff_roles_org_id", "staff_roles", ["org_id"])
    op.create_index(
        "uq_staff_roles_org_name_ci",
        "staff_roles",
        ["org_id", sa.text("lower(name)")],
        unique=True,
    )


def _apply_rls() -> None:
    for table in ("memberships", "departments", "staff_roles"):
        for statement in rls.rls_statements(table):
            _exec(statement)


def _install_self_accept_guard() -> None:
    _exec(
        """
        CREATE OR REPLACE FUNCTION public.membership_self_accept_guard()
        RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            v_user text := current_setting('app.current_user_id', true);
            v_is_invitee boolean := false;
        BEGIN
            IF OLD.status = 'pending' AND NEW.status = 'active' THEN
                BEGIN
                    v_is_invitee :=
                        v_user IS NOT NULL
                        AND v_user <> ''
                        AND v_user::uuid = OLD.user_id;
                EXCEPTION WHEN others THEN
                    v_is_invitee := false;
                END;

                IF NOT v_is_invitee THEN
                    RAISE EXCEPTION
                        'pending membership can only be activated by the invitee'
                        USING ERRCODE = '42501';
                END IF;

                IF (
                    NEW.user_id IS DISTINCT FROM OLD.user_id
                    OR NEW.org_id IS DISTINCT FROM OLD.org_id
                    OR NEW.role IS DISTINCT FROM OLD.role
                    OR NEW.is_default IS DISTINCT FROM OLD.is_default
                    OR NEW.job_title IS DISTINCT FROM OLD.job_title
                    OR NEW.department_id IS DISTINCT FROM OLD.department_id
                    OR NEW.reports_to_id IS DISTINCT FROM OLD.reports_to_id
                ) THEN
                    RAISE EXCEPTION
                        'pending membership can only be activated by the invitee'
                        USING ERRCODE = '42501';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    _exec(
        "DROP TRIGGER IF EXISTS memberships_self_accept_guard ON memberships"
    )
    _exec(
        "CREATE TRIGGER memberships_self_accept_guard "
        "BEFORE UPDATE ON memberships FOR EACH ROW "
        "EXECUTE FUNCTION public.membership_self_accept_guard()"
    )


def _grant_app_role() -> None:
    _exec(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    _exec(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON "
        f"departments, staff_roles TO {APP_ROLE}"
    )


def upgrade() -> None:
    _create_staff_tables()
    _upgrade_memberships()
    _apply_rls()
    _install_self_accept_guard()
    _grant_app_role()


def downgrade() -> None:
    _exec("DROP TRIGGER IF EXISTS memberships_self_accept_guard ON memberships")
    _exec("DROP FUNCTION IF EXISTS public.membership_self_accept_guard()")

    # Restore the 005 membership policies before dropping the status column
    # they reference.
    for name in rls.membership_policy_names():
        _exec(f"DROP POLICY IF EXISTS {name} ON memberships")
    select_expr = (
        "org_id = current_setting('app.current_org_id', true)::uuid"
        " OR user_id = current_setting('app.current_user_id', true)::uuid"
    )
    write_expr = (
        "org_id = current_setting('app.current_org_id', true)::uuid"
        " AND current_setting('app.current_role', true) IN ('owner', 'admin')"
    )
    _exec(
        "CREATE POLICY memberships_select ON memberships FOR SELECT "
        f"USING ({select_expr})"
    )
    _exec(
        "CREATE POLICY memberships_insert ON memberships FOR INSERT "
        f"WITH CHECK ({write_expr})"
    )
    _exec(
        "CREATE POLICY memberships_update ON memberships FOR UPDATE "
        f"USING ({write_expr}) WITH CHECK ({write_expr})"
    )
    _exec(
        "CREATE POLICY memberships_delete ON memberships FOR DELETE "
        f"USING ({write_expr})"
    )

    _exec("ALTER TABLE memberships DROP CONSTRAINT IF EXISTS ck_memberships_reports_to_not_self")
    op.drop_index("ix_memberships_org_status", table_name="memberships")
    op.drop_index("ix_memberships_reports_to_id", table_name="memberships")
    op.drop_index("ix_memberships_department_id", table_name="memberships")
    op.drop_index("ix_memberships_status", table_name="memberships")
    op.drop_constraint(
        "fk_memberships_reports_to_id_memberships",
        "memberships",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_memberships_department_id_departments",
        "memberships",
        type_="foreignkey",
    )
    op.drop_column("memberships", "reports_to_id")
    op.drop_column("memberships", "department_id")
    op.drop_column("memberships", "job_title")
    op.drop_column("memberships", "status")

    for table in ("staff_roles", "departments"):
        for statement in rls.disable_rls_statements(table):
            _exec(statement)
        op.drop_table(table)

    _exec("DROP TYPE IF EXISTS membership_status")
