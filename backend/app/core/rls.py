"""
Row Level Security helpers — the single source of truth.

- GUC (Postgres session setting) names carrying request identity into the DB.
- Policy DDL generation used by migrations and verified by the RLS tests.
- The dedicated, non-superuser app role (``finex_app``) that cannot bypass RLS.

Security invariants:
- Every table with an ``org_id`` column is RLS enabled AND forced, so even the
  table owner is subject to policies (superusers still are not — that is why
  the runtime app must connect as the ``finex_app`` role in production).
- Policies deny by default: when a GUC is unset,
  ``current_setting(name, true)`` returns NULL, comparisons yield NULL and the
  row is filtered out (for ``WITH CHECK`` a NULL result raises an error).
"""

from __future__ import annotations

import uuid

from sqlalchemy import MetaData, func, select
from sqlalchemy.ext.asyncio import AsyncSession

# Postgres session settings (GUCs) bound per transaction by get_rls_session().
GUC_ORG = "app.current_org_id"
GUC_USER = "app.current_user_id"
GUC_ROLE = "app.current_role"
# Explicit, transaction-scoped escape hatch for the audit append-only trigger.
# Only sanctioned maintenance paths (retention purge, org teardown) set this.
GUC_ALLOW_AUDIT_PURGE = "app.allow_audit_purge"

# Non-superuser role used by the application runtime (created by the initial
# migration). Can be granted LOGIN out-of-band in hardened environments.
APP_DB_ROLE = "finex_app"


def default_policy_expression() -> str:
    return f"org_id = current_setting('{GUC_ORG}', true)::uuid"


# Per-table policy overrides. Default policy is strict org isolation.
_POLICY_OVERRIDES: dict[str, str] = {
    # Memberships: a user must be able to read their own memberships while
    # logging in — i.e. before an active org exists. Rows are visible when
    # scoped to the active org OR owned by the current user.
    "memberships": (
        f"org_id = current_setting('{GUC_ORG}', true)::uuid"
        f" OR user_id = current_setting('{GUC_USER}', true)::uuid"
    ),
}


def policy_expression(table_name: str) -> str:
    return _POLICY_OVERRIDES.get(table_name, default_policy_expression())


def policy_name(table_name: str) -> str:
    return f"{table_name}_tenant_isolation"


def tenant_table_names(metadata: MetaData) -> list[str]:
    """Names of tables that carry an ``org_id`` column → must be RLS-protected."""
    return [t.name for t in metadata.sorted_tables if "org_id" in t.columns]


def rls_statements(table_name: str) -> list[str]:
    """Idempotent DDL statements that put a tenant table under RLS."""
    expr = policy_expression(table_name)
    return [
        f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS {policy_name(table_name)} ON {table_name}",
        (
            f"CREATE POLICY {policy_name(table_name)} ON {table_name} "
            f"USING ({expr}) WITH CHECK ({expr})"
        ),
    ]


def disable_rls_statements(table_name: str) -> list[str]:
    """DDL to remove RLS protection (downgrade only)."""
    return [
        f"DROP POLICY IF EXISTS {policy_name(table_name)} ON {table_name}",
        f"ALTER TABLE {table_name} NO FORCE ROW LEVEL SECURITY",
        f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY",
    ]


async def bind_session_context(
    session: AsyncSession,
    *,
    org_id: uuid.UUID | str | None = None,
    user_id: uuid.UUID | str | None = None,
    role: str | None = None,
) -> None:
    """
    Bind request-identity GUCs for the *current transaction*.

    Uses ``set_config(name, value, is_local=true)`` — the parameterizable
    equivalent of ``SET LOCAL`` (Postgres does not allow bind parameters in
    ``SET``). Transaction scoping means context never leaks to the next
    borrower of a pooled connection.
    """
    if org_id is not None:
        await session.execute(select(func.set_config(GUC_ORG, str(org_id), True)))
    if user_id is not None:
        await session.execute(select(func.set_config(GUC_USER, str(user_id), True)))
    if role is not None:
        await session.execute(select(func.set_config(GUC_ROLE, str(role), True)))


async def enable_audit_purge(session: AsyncSession) -> None:
    """
    Allow UPDATE/DELETE on audit_logs for the current transaction.

    Sanctioned maintenance only (retention purge, org teardown). Request
    paths must never call this.
    """
    await session.execute(select(func.set_config(GUC_ALLOW_AUDIT_PURGE, "on", True)))
