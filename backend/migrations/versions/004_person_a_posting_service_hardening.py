"""Posting-service hardening (Person A).

Revision ID: 004_posting_hardening
Revises: 003_posting_service
Create Date: 2026-10-22

Executes ``db/migrations/0003_person_a_posting_service_hardening.sql``:

* the posting functions check ``app.current_org_id`` before any privileged
  write and before returning an idempotent replay;
* journal and stock retries return the original row, even after a period is
  closed or the entry has been reversed;
* zero-cost sales are allowed (there is no inventory value to relieve);
* ``ledger.update_account`` supports account setup without changing an
  account's immutable code or type.

The revision also extends the tenant guard to every ledger table that carries
``org_id``.  The SECURITY DEFINER functions run as the schema owner and bypass
RLS, so the guards are the second line of defence against a caller passing a
foreign organisation id.

Forward-only note: downgrade removes the new function and the extra triggers,
but it intentionally leaves the safer function bodies in place.  Restoring the
older bodies would re-open replay bugs; no data is changed by this revision.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence, Union

from alembic import op

from app.core import rls
from migrations.sql_utils import split_sql_statements

# revision identifiers, used by Alembic.
revision: str = "004_posting_hardening"
down_revision: Union[str, Sequence[str], None] = "003_posting_service"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ``backend/migrations/versions/004_*.py`` -> repo root.
REPO_ROOT = Path(__file__).resolve().parents[3]
HARDENING_SQL = (
    REPO_ROOT / "db" / "migrations" / "0003_person_a_posting_service_hardening.sql"
)

APP_ROLE = rls.APP_DB_ROLE

# Every ledger table that carries org_id.  receipt_lines is isolated through
# its parent receipt and has no org_id column.
GUARDED_TABLES = [
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
    "sale_lines",
    "suppliers",
]


def _exec(statement: str) -> None:
    op.get_bind().exec_driver_sql(statement)


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
    for table in GUARDED_TABLES:
        trigger = f"{table}_tenant_guard"
        _exec(f"DROP TRIGGER IF EXISTS {trigger} ON ledger.{table}")
        _exec(
            f"CREATE TRIGGER {trigger} BEFORE INSERT OR UPDATE ON ledger.{table} "
            f"FOR EACH ROW EXECUTE FUNCTION ledger.enforce_org_context()"
        )


def _grant_app_role() -> None:
    _exec(f"GRANT USAGE ON SCHEMA ledger TO {APP_ROLE}")
    _exec("REVOKE ALL ON ALL TABLES IN SCHEMA ledger FROM PUBLIC")
    _exec("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA ledger FROM PUBLIC")
    _exec(f"REVOKE ALL ON ALL TABLES IN SCHEMA ledger FROM {APP_ROLE}")
    _exec(f"GRANT SELECT ON ALL TABLES IN SCHEMA ledger TO {APP_ROLE}")
    _exec(f"REVOKE ALL ON TABLE ledger.org_sequences FROM {APP_ROLE}")
    _exec(f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA ledger TO {APP_ROLE}")


def upgrade() -> None:
    if not HARDENING_SQL.exists():
        raise RuntimeError(
            f"Posting hardening SQL not found at {HARDENING_SQL}. "
            "Run Alembic from a full checkout of the FinEx repository."
        )

    sql = HARDENING_SQL.read_text(encoding="utf-8")
    for statement in split_sql_statements(sql):
        _exec(statement)

    _apply_tenant_guards()
    _grant_app_role()


def downgrade() -> None:
    # The function replacements are deliberately left in place (see the
    # module docstring); only objects introduced by this revision are removed.
    _exec("DROP FUNCTION IF EXISTS ledger.update_account(uuid, uuid, text, boolean, boolean)")

    for table in GUARDED_TABLES:
        if table in {"posting_events", "stock_movements", "receipts", "sale_lines", "suppliers"}:
            # Restore the INSERT-only guards created by revisions 002/003.
            trigger = f"{table}_tenant_guard"
            _exec(f"DROP TRIGGER IF EXISTS {trigger} ON ledger.{table}")
            _exec(
                f"CREATE TRIGGER {trigger} BEFORE INSERT ON ledger.{table} "
                f"FOR EACH ROW EXECUTE FUNCTION ledger.enforce_org_context()"
            )
        else:
            _exec(
                f"DROP TRIGGER IF EXISTS {table}_tenant_guard ON ledger.{table}"
            )
