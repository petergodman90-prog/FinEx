"""Financial core (Person A) + tenant isolation (Person B).

Revision ID: 002_financial_core
Revises: 001_initial
Create Date: 2026-10-20

Executes Person A's ``db/migrations/0001_person_a_financial_core.sql`` as part
of the single Alembic stream, then closes the tenant-isolation gaps around it:

* RLS enabled *and forced* on every ``ledger`` table that carries ``org_id``;
  ``receipt_lines`` is isolated through its parent receipt.
* The read views run with ``security_invoker = true``. Without this a view is
  executed with the view owner's privileges and would expose every tenant.
* The restricted ``finex_app`` role gets SELECT on the ledger read surface and
  EXECUTE on the posting functions — never INSERT/UPDATE/DELETE on the tables.
  ``ledger.org_sequences`` stays internal.
* Tenant guards on the posting write paths reject a row whose ``org_id`` does
  not match the transaction's ``app.current_org_id``. This protects against a
  caller passing a foreign org id even when the SECURITY DEFINER functions run
  as a privileged owner.

Production note: the posting functions are SECURITY DEFINER and are expected to
be owned by the migration/schema owner (superuser or BYPASSRLS), while the
application connects as ``finex_app``. The guards above are the second line of
defence when that owner bypasses RLS.

Downgrade drops the whole ``ledger`` schema; the ``pgcrypto`` extension is left
in place because other domains may rely on it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence, Union

from alembic import op

from app.core import rls
from migrations.sql_utils import split_sql_statements

# revision identifiers, used by Alembic.
revision: str = "002_financial_core"
down_revision: Union[str, Sequence[str], None] = "001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ``backend/migrations/versions/002_*.py`` -> repo root.
REPO_ROOT = Path(__file__).resolve().parents[3]
FINANCIAL_CORE_SQL = REPO_ROOT / "db" / "migrations" / "0001_person_a_financial_core.sql"

APP_ROLE = rls.APP_DB_ROLE

# ledger tables carrying org_id directly. receipt_lines is handled separately.
LEDGER_TENANT_TABLES = [
    "accounts",
    "accounting_periods",
    "org_sequences",
    "posting_events",
    "journal_entries",
    "journal_lines",
    "products",
    "stock_movements",
    "open_items",
    "payment_allocations",
    "receipts",
]

# Write paths that must carry the transaction's org context. The posting
# functions insert these rows; a mismatched org is rejected before it lands.
GUARDED_WRITE_TABLES = ["posting_events", "stock_movements", "receipts"]

ORG_EXPR = rls.default_policy_expression()
ORG_FROM_GUC = f"current_setting('{rls.GUC_ORG}', true)::uuid"
RECEIPT_LINES_EXPR = (
    "EXISTS (SELECT 1 FROM ledger.receipts r "
    "WHERE r.id = receipt_lines.receipt_id "
    f"AND r.org_id = {ORG_FROM_GUC})"
)


def _exec(statement: str) -> None:
    # exec_driver_sql avoids SQLAlchemy text() bind-parameter parsing (the SQL
    # contains casts like ::uuid and RAISE format placeholders).
    op.get_bind().exec_driver_sql(statement)


def _apply_rls() -> None:
    for table in LEDGER_TENANT_TABLES:
        policy = f"{table}_tenant_isolation"
        _exec(f"ALTER TABLE ledger.{table} ENABLE ROW LEVEL SECURITY")
        _exec(f"ALTER TABLE ledger.{table} FORCE ROW LEVEL SECURITY")
        _exec(f"DROP POLICY IF EXISTS {policy} ON ledger.{table}")
        _exec(
            f"CREATE POLICY {policy} ON ledger.{table} "
            f"USING ({ORG_EXPR}) WITH CHECK ({ORG_EXPR})"
        )

    policy = "receipt_lines_tenant_isolation"
    _exec("ALTER TABLE ledger.receipt_lines ENABLE ROW LEVEL SECURITY")
    _exec("ALTER TABLE ledger.receipt_lines FORCE ROW LEVEL SECURITY")
    _exec(f"DROP POLICY IF EXISTS {policy} ON ledger.receipt_lines")
    _exec(
        f"CREATE POLICY {policy} ON ledger.receipt_lines "
        f"USING ({RECEIPT_LINES_EXPR}) WITH CHECK ({RECEIPT_LINES_EXPR})"
    )


def _apply_tenant_guards() -> None:
    _exec(
        """
        CREATE OR REPLACE FUNCTION ledger.enforce_org_context() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            v_ctx text := current_setting('app.current_org_id', true);
        BEGIN
            IF v_ctx IS NOT NULL AND v_ctx <> '' AND NEW.org_id::text <> v_ctx THEN
                RAISE EXCEPTION
                    'organisation context mismatch: row org % does not match session org %',
                    NEW.org_id, v_ctx
                    USING ERRCODE = '42501';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    for table in GUARDED_WRITE_TABLES:
        trigger = f"{table}_tenant_guard"
        _exec(f"DROP TRIGGER IF EXISTS {trigger} ON ledger.{table}")
        _exec(
            f"CREATE TRIGGER {trigger} BEFORE INSERT ON ledger.{table} "
            f"FOR EACH ROW EXECUTE FUNCTION ledger.enforce_org_context()"
        )


def _secure_views() -> None:
    # PG15 view option. Without security_invoker the views execute as their
    # owner and bypass the RLS policies on the underlying ledger tables.
    for view in ("v_trial_balance", "v_inventory_on_hand", "v_open_item_balances"):
        _exec(f"ALTER VIEW ledger.{view} SET (security_invoker = true)")


def _grant_app_role() -> None:
    _exec(f"GRANT USAGE ON SCHEMA ledger TO {APP_ROLE}")
    _exec(f"REVOKE ALL ON ALL TABLES IN SCHEMA ledger FROM PUBLIC")
    _exec(f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA ledger FROM PUBLIC")
    _exec(f"REVOKE ALL ON ALL TABLES IN SCHEMA ledger FROM {APP_ROLE}")
    _exec(f"GRANT SELECT ON ALL TABLES IN SCHEMA ledger TO {APP_ROLE}")
    # Internal per-org counters are not part of the read surface.
    _exec(f"REVOKE ALL ON TABLE ledger.org_sequences FROM {APP_ROLE}")
    # Writes happen only through the posting functions.
    _exec(f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA ledger TO {APP_ROLE}")
    _exec(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA ledger "
        f"GRANT SELECT ON TABLES TO {APP_ROLE}"
    )
    _exec(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA ledger "
        f"GRANT EXECUTE ON FUNCTIONS TO {APP_ROLE}"
    )


def upgrade() -> None:
    """Install the financial core and its tenant-isolation layer."""
    if not FINANCIAL_CORE_SQL.exists():
        raise RuntimeError(
            f"Financial-core SQL not found at {FINANCIAL_CORE_SQL}. "
            "Run Alembic from a full checkout of the FinEx repository."
        )

    bind = op.get_bind()
    already_present = bind.exec_driver_sql(
        "SELECT 1 FROM information_schema.schemata WHERE schema_name = 'ledger'"
    ).scalar()
    if already_present:
        raise RuntimeError(
            "The ledger schema already exists, but Alembic is not at this "
            "revision. If it was applied manually, drop the schema (or stamp "
            "this revision) before upgrading; do not apply it twice."
        )

    sql = FINANCIAL_CORE_SQL.read_text(encoding="utf-8")
    for statement in split_sql_statements(sql):
        _exec(statement)

    _apply_tenant_guards()
    _apply_rls()
    _secure_views()
    _grant_app_role()


def downgrade() -> None:
    """Remove the financial core (destructive; financial history is lost)."""
    _exec("DROP SCHEMA IF EXISTS ledger CASCADE")
