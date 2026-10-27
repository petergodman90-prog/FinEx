"""Posting-service support layer (Person A).

Revision ID: 003_posting_service
Revises: 002_financial_core
Create Date: 2026-10-22

Executes Person A's ``db/migrations/0002_person_a_posting_service.sql`` and
closes the tenant-isolation gaps around it:

* RLS enabled *and forced* on the new ``ledger.sale_lines`` table, plus the
  tenant guard that rejects a row whose ``org_id`` does not match the
  transaction's ``app.current_org_id``;
* ``ledger.v_open_item_balances`` is re-created by the migration, so its
  ``security_invoker`` option is re-asserted;
* the restricted ``finex_app`` role receives SELECT on the new table and
  EXECUTE on the new posting functions — still never INSERT/UPDATE/DELETE.

The result is that every financial or stock write in the platform is a call
to a SECURITY DEFINER function. A UI, importer, AI assistant or WhatsApp
integration that tries to write a ledger row directly is rejected by the
database, not merely by convention.

Downgrade removes the new capabilities. It cannot restore ``CHECK (amount >
0)`` on ``payment_allocations`` once a settlement has been reversed, so a
downgrade after reversals fails loudly rather than silently losing history.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence, Union

from alembic import op

from app.core import rls
from migrations.sql_utils import split_sql_statements

# revision identifiers, used by Alembic.
revision: str = "003_posting_service"
down_revision: Union[str, Sequence[str], None] = "002_financial_core"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ``backend/migrations/versions/003_*.py`` -> repo root.
REPO_ROOT = Path(__file__).resolve().parents[3]
POSTING_SERVICE_SQL = REPO_ROOT / "db" / "migrations" / "0002_person_a_posting_service.sql"

APP_ROLE = rls.APP_DB_ROLE

ORG_EXPR = rls.default_policy_expression()
ORG_FROM_GUC = f"current_setting('{rls.GUC_ORG}', true)::uuid"

# The migration adds two tables; the rest of the ledger was protected by 002.
NEW_TENANT_TABLES = ["sale_lines", "suppliers"]
GUARDED_WRITE_TABLES = ["sale_lines", "suppliers"]

# Functions introduced by this revision.
NEW_FUNCTIONS = [
    "ledger.create_account(uuid, text, text, ledger.account_type, boolean, boolean)",
    "ledger.seed_default_chart_of_accounts(uuid)",
    "ledger.create_supplier(uuid, text, text)",
    "ledger.create_product(uuid, text, text, text, numeric, numeric, numeric)",
    "ledger.update_product(uuid, uuid, numeric, numeric, numeric, boolean)",
    "ledger.create_open_item(uuid, ledger.open_item_type, uuid, uuid, numeric, date)",
    "ledger.allocate_payment(uuid, uuid, uuid, numeric, uuid)",
    "ledger.create_accounting_period(uuid, date, date)",
    "ledger.set_accounting_period_state(uuid, date, date, boolean)",
    "ledger.create_receipt_document(uuid, uuid, char, jsonb, uuid, text, ledger.receipt_type, uuid)",
    "ledger.issue_credit_note(uuid, uuid, char, jsonb, uuid, text, uuid)",
    "ledger.post_sale_lines(uuid, uuid, jsonb)",
]


def _exec(statement: str) -> None:
    # exec_driver_sql avoids SQLAlchemy text() bind-parameter parsing (the SQL
    # contains casts like ::uuid and RAISE format placeholders).
    op.get_bind().exec_driver_sql(statement)


def _apply_rls() -> None:
    for table in NEW_TENANT_TABLES:
        policy = f"{table}_tenant_isolation"
        _exec(f"ALTER TABLE ledger.{table} ENABLE ROW LEVEL SECURITY")
        _exec(f"ALTER TABLE ledger.{table} FORCE ROW LEVEL SECURITY")
        _exec(f"DROP POLICY IF EXISTS {policy} ON ledger.{table}")
        _exec(
            f"CREATE POLICY {policy} ON ledger.{table} "
            f"USING ({ORG_EXPR}) WITH CHECK ({ORG_EXPR})"
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
    # The migration re-creates this view; assert the option is still in place.
    for view in ("v_trial_balance", "v_inventory_on_hand", "v_open_item_balances"):
        _exec(f"ALTER VIEW ledger.{view} SET (security_invoker = true)")


def _grant_app_role() -> None:
    _exec(f"GRANT USAGE ON SCHEMA ledger TO {APP_ROLE}")
    _exec(f"REVOKE ALL ON ALL TABLES IN SCHEMA ledger FROM PUBLIC")
    _exec(f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA ledger FROM PUBLIC")
    _exec(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA ledger FROM PUBLIC")
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
    """Install the posting-service support layer."""
    if not POSTING_SERVICE_SQL.exists():
        raise RuntimeError(
            f"Posting-service SQL not found at {POSTING_SERVICE_SQL}. "
            "Run Alembic from a full checkout of the FinEx repository."
        )

    sql = POSTING_SERVICE_SQL.read_text(encoding="utf-8")
    for statement in split_sql_statements(sql):
        _exec(statement)

    _apply_tenant_guards()
    _apply_rls()
    _secure_views()
    _grant_app_role()


def downgrade() -> None:
    """Remove the posting-service support layer."""
    for signature in NEW_FUNCTIONS:
        _exec(f"DROP FUNCTION IF EXISTS {signature}")

    _exec("DROP TABLE IF EXISTS ledger.sale_lines")
    _exec("DROP TABLE IF EXISTS ledger.suppliers")

    _exec("ALTER TABLE ledger.payment_allocations DROP COLUMN IF EXISTS reversal_of_id")
    _exec(
        "ALTER TABLE ledger.payment_allocations "
        "DROP CONSTRAINT IF EXISTS payment_allocations_amount_check"
    )
    _exec(
        "ALTER TABLE ledger.payment_allocations "
        "ADD CONSTRAINT payment_allocations_amount_check CHECK (amount > 0)"
    )

    _exec(
        "ALTER TABLE ledger.receipts "
        "DROP CONSTRAINT IF EXISTS receipts_credit_note_only_for_credit_notes"
    )
    _exec(
        "ALTER TABLE ledger.receipts "
        "DROP CONSTRAINT IF EXISTS receipts_credit_note_not_self"
    )
    _exec(
        "ALTER TABLE ledger.receipts "
        "DROP CONSTRAINT IF EXISTS receipts_credit_note_of_fk"
    )
    _exec("ALTER TABLE ledger.receipts DROP COLUMN IF EXISTS credit_note_of_id")
    _exec("ALTER TABLE ledger.receipts DROP COLUMN IF EXISTS receipt_type")
    _exec("DROP TYPE IF EXISTS ledger.receipt_type")

    _exec("ALTER TABLE ledger.products DROP COLUMN IF EXISTS current_cost")
    _exec("ALTER TABLE ledger.accounts DROP COLUMN IF EXISTS is_cash_account")

    # Restore the original aging view. CREATE OR REPLACE VIEW cannot drop the
    # column this revision added, so the read model is dropped and recreated.
    _exec("DROP VIEW IF EXISTS ledger.v_open_item_balances")
    _exec(
        """
        CREATE VIEW ledger.v_open_item_balances AS
        SELECT
            oi.org_id,
            oi.id AS open_item_id,
            oi.item_type,
            oi.counterparty_id,
            oi.original_amount,
            coalesce(sum(pa.amount), 0) AS allocated_amount,
            oi.original_amount - coalesce(sum(pa.amount), 0) AS outstanding_amount,
            oi.due_on,
            CASE
                WHEN oi.due_on IS NULL OR current_date <= oi.due_on THEN '0-30'
                WHEN current_date - oi.due_on <= 30 THEN '31-60'
                WHEN current_date - oi.due_on <= 60 THEN '61-90'
                ELSE '90+'
            END AS aging_bucket
        FROM ledger.open_items oi
        LEFT JOIN ledger.payment_allocations pa ON pa.open_item_id = oi.id
        GROUP BY oi.org_id, oi.id, oi.item_type, oi.counterparty_id,
                 oi.original_amount, oi.due_on
        """
    )
    _exec("ALTER VIEW ledger.v_open_item_balances SET (security_invoker = true)")
