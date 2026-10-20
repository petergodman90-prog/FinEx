"""Initial schema: tenancy core, CRM, proposals & audit + RLS policies.

Revision ID: 001_initial
Revises:
Create Date: 2026-10-17

Creates every foundational table, applies Row Level Security (enabled +
forced) to every tenant table, makes audit_logs append-only via a statement
trigger, and provisions the RLS-restricted application role (``finex_app``).

Policy/DDL generation lives in ``app/core/rls.py`` so migrations and the RLS
test-suite share a single source of truth.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.core import rls

# revision identifiers, used by Alembic.
revision: str = "001_initial"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Tables carrying an org_id column. Verified against Base.metadata by the
# RLS test-suite (tests/test_rls.py) so a new model can never ship without
# RLS by accident.
TENANT_TABLES = ["audit_logs", "customers", "memberships", "proposals", "touchpoints"]

APP_ROLE = rls.APP_DB_ROLE  # finex_app


def upgrade() -> None:
    """Upgrade schema."""
    # ── tables ─────────────────────────────────────────────
    op.create_table(
        "organizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default=sa.text("'USD'")),
        sa.Column("timezone", sa.String(64), nullable=False, server_default=sa.text("'UTC'")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("settings", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_organizations_slug", "organizations", ["slug"], unique=True)

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("email_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("email_bidx", sa.String(64), nullable=False),
        sa.Column("phone_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("phone_bidx", sa.String(64), nullable=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_users_email_bidx", "users", ["email_bidx"], unique=True)
    op.create_index("ix_users_phone_bidx", "users", ["phone_bidx"])
    op.create_index("ix_users_deleted_at", "users", ["deleted_at"])

    op.create_table(
        "memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role",
            sa.Enum("owner", "admin", "manager", "staff", "viewer", name="membership_role"),
            nullable=False,
        ),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("org_id", "user_id", name="uq_memberships_org_user"),
    )
    op.create_index("ix_memberships_org_id", "memberships", ["org_id"])
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"])
    op.create_index("ix_memberships_role", "memberships", ["role"])

    op.create_table(
        "customers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("email_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("email_bidx", sa.String(64), nullable=True),
        sa.Column("phone_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("phone_bidx", sa.String(64), nullable=True),
        sa.Column("company", sa.String(200), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("tags", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("source", sa.String(40), nullable=False, server_default=sa.text("'manual'")),
        sa.Column(
            "merged_into_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_customers_org_id", "customers", ["org_id"])
    op.create_index("ix_customers_name", "customers", ["name"])
    op.create_index("ix_customers_email_bidx", "customers", ["email_bidx"])
    op.create_index("ix_customers_phone_bidx", "customers", ["phone_bidx"])
    op.create_index("ix_customers_deleted_at", "customers", ["deleted_at"])
    op.create_index(
        "uq_customers_org_phone_bidx_active",
        "customers",
        ["org_id", "phone_bidx"],
        unique=True,
        postgresql_where=sa.text("phone_bidx IS NOT NULL AND deleted_at IS NULL"),
    )
    op.create_index(
        "uq_customers_org_email_bidx_active",
        "customers",
        ["org_id", "email_bidx"],
        unique=True,
        postgresql_where=sa.text("email_bidx IS NOT NULL AND deleted_at IS NULL"),
    )

    op.create_table(
        "touchpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("customers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("ref_type", sa.String(50), nullable=True),
        sa.Column("ref_id", sa.String(64), nullable=True),
        sa.Column("meta", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_touchpoints_org_id", "touchpoints", ["org_id"])
    op.create_index("ix_touchpoints_customer_id", "touchpoints", ["customer_id"])

    op.create_table(
        "proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("proposal_type", sa.String(50), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("source", sa.String(20), nullable=False, server_default=sa.text("'manual'")),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "approved", "rejected", "expired", "applied",
                name="proposal_status",
            ),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("idempotency_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "decided_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_proposals_org_id", "proposals", ["org_id"])
    op.create_index("ix_proposals_proposal_type", "proposals", ["proposal_type"])
    op.create_index("ix_proposals_status", "proposals", ["status"])
    op.create_index("ix_proposals_expires_at", "proposals", ["expires_at"])
    op.create_index("ix_proposals_deleted_at", "proposals", ["deleted_at"])
    op.create_index(
        "uq_proposals_org_idem_pending",
        "proposals",
        ["org_id", "idempotency_hash"],
        unique=True,
        postgresql_where=sa.text("status = 'pending' AND deleted_at IS NULL"),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("actor_role", sa.String(20), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=True),
        sa.Column("entity_id", sa.String(64), nullable=True),
        sa.Column("before_state", postgresql.JSONB(), nullable=True),
        sa.Column("after_state", postgresql.JSONB(), nullable=True),
        sa.Column("meta", postgresql.JSONB(), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
    )
    op.create_index("ix_audit_logs_org_id", "audit_logs", ["org_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_entity_type", "audit_logs", ["entity_type"])
    op.create_index("ix_audit_logs_entity_id", "audit_logs", ["entity_id"])
    op.create_index("ix_audit_logs_org_created", "audit_logs", ["org_id", "created_at"])

    # ── Row Level Security on every tenant table ───────────
    for table in TENANT_TABLES:
        for statement in rls.rls_statements(table):
            op.execute(statement)

    # ── audit_logs is append-only ──────────────────────────
    # UPDATE/DELETE raise by default. The only escape hatch is an explicit,
    # transaction-scoped GUC (app.allow_audit_purge = 'on') used by sanctioned
    # retention purges / org teardown — never set by request paths.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION finex_audit_logs_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF current_setting('app.allow_audit_purge', true) = 'on' THEN
                RETURN NULL;
            END IF;
            RAISE EXCEPTION
                'audit_logs is append-only; UPDATE and DELETE are not permitted';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_logs_immutable
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH STATEMENT EXECUTE FUNCTION finex_audit_logs_immutable();
        """
    )

    # ── RLS-restricted application role ────────────────────
    # The app connects as this role in hardened/production setups (see
    # .env.example APP comment). It can never BYPASSRLS; GRANTs are limited
    # to DML and audit_logs stays append-only.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
            END IF;
        END
        $$;
        """
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            BEGIN
                EXECUTE 'GRANT {APP_ROLE} TO CURRENT_USER';
            EXCEPTION WHEN insufficient_privilege THEN
                RAISE NOTICE 'Skipping GRANT {APP_ROLE} TO CURRENT_USER (insufficient privilege)';
            END;
        END
        $$;
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}"
    )
    op.execute(f"REVOKE UPDATE, DELETE ON audit_logs FROM {APP_ROLE}")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER IF EXISTS audit_logs_immutable ON audit_logs")
    op.execute("DROP FUNCTION IF EXISTS finex_audit_logs_immutable()")

    # Policies die with their tables; drop in reverse FK order.
    for table in ("audit_logs", "proposals", "touchpoints", "customers", "memberships", "users", "organizations"):
        op.drop_table(table)

    op.execute("DROP TYPE IF EXISTS proposal_status")
    op.execute("DROP TYPE IF EXISTS membership_role")

    # Cluster-scoped role: best-effort removal (left in place with a NOTICE
    # when other objects still depend on it).
    op.execute(
        f"""
        DO $$
        BEGIN
            BEGIN
                DROP ROLE IF EXISTS {APP_ROLE};
            EXCEPTION WHEN dependent_objects_still_exist OR insufficient_privilege THEN
                RAISE NOTICE 'Role {APP_ROLE} left in place';
            END;
        END
        $$;
        """
    )
