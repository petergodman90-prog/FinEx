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
# Memberships need a SELECT exception so a user can see their own memberships
# before an active org exists. Writes are deliberately not self-service: only
# an owner/admin acting in the active org may add, change or remove a
# membership. Otherwise a staff row could promote itself to owner.
_MEMBERSHIP_SELECT = (
    f"org_id = current_setting('{GUC_ORG}', true)::uuid"
    f" OR user_id = current_setting('{GUC_USER}', true)::uuid"
)
# Owner/admin only. Admins must not touch an owner row, and a pending invite
# may only be activated/deleted by the invitee (see the update/delete
# policies below). The self-accept guard trigger in migration 006 prevents
# the invitee from changing any field other than ``status`` while accepting.
_MEMBERSHIP_WRITE = (
    f"org_id = current_setting('{GUC_ORG}', true)::uuid"
    " AND ("
    f"current_setting('{GUC_ROLE}', true) = 'owner'"
    " OR ("
    f"current_setting('{GUC_ROLE}', true) = 'admin'"
    " AND role NOT IN ('owner', 'admin'))"
    ")"
)
_MEMBERSHIP_SELF_PENDING = (
    f"user_id = current_setting('{GUC_USER}', true)::uuid"
    " AND status = 'pending'"
)
_MEMBERSHIP_SELF_ACTIVE = (
    f"user_id = current_setting('{GUC_USER}', true)::uuid"
    " AND status = 'active'"
)
_STAFF_READ_ROLES = "'manager', 'admin', 'owner'"
_STAFF_WRITE_ROLES = "'owner', 'admin'"
_STAFF_TABLES = {"departments", "staff_roles"}
_STAFF_READ = (
    f"org_id = current_setting('{GUC_ORG}', true)::uuid"
    f" AND current_setting('{GUC_ROLE}', true) IN ({_STAFF_READ_ROLES})"
)
_STAFF_WRITE = (
    f"org_id = current_setting('{GUC_ORG}', true)::uuid"
    f" AND current_setting('{GUC_ROLE}', true) IN ({_STAFF_WRITE_ROLES})"
)
_POLICY_OVERRIDES: dict[str, str] = {
    "memberships": _MEMBERSHIP_SELECT,
    "departments": _STAFF_READ,
    "staff_roles": _STAFF_READ,
}


def policy_expression(table_name: str) -> str:
    return _POLICY_OVERRIDES.get(table_name, default_policy_expression())


def policy_name(table_name: str) -> str:
    return f"{table_name}_tenant_isolation"


def membership_policy_names() -> list[str]:
    return [
        "memberships_select",
        "memberships_insert",
        "memberships_update",
        "memberships_delete",
    ]


def staff_policy_names(table_name: str) -> list[str]:
    return [
        f"{table_name}_select",
        f"{table_name}_insert",
        f"{table_name}_update",
        f"{table_name}_delete",
    ]


def tenant_table_names(metadata: MetaData) -> list[str]:
    """Names of tables that carry an ``org_id`` column → must be RLS-protected."""
    return [t.name for t in metadata.sorted_tables if "org_id" in t.columns]


def rls_statements(table_name: str) -> list[str]:
    """Idempotent DDL statements that put a tenant table under RLS."""
    if table_name == "memberships":
        return [
            "ALTER TABLE memberships ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE memberships FORCE ROW LEVEL SECURITY",
            f"DROP POLICY IF EXISTS {policy_name('memberships')} ON memberships",
            *(
                f"DROP POLICY IF EXISTS {name} ON memberships"
                for name in membership_policy_names()
            ),
            (
                "CREATE POLICY memberships_select ON memberships FOR SELECT "
                f"USING ({_MEMBERSHIP_SELECT})"
            ),
            (
                "CREATE POLICY memberships_insert ON memberships FOR INSERT "
                f"WITH CHECK ({_MEMBERSHIP_WRITE})"
            ),
            (
                "CREATE POLICY memberships_update ON memberships FOR UPDATE "
                f"USING (({_MEMBERSHIP_WRITE}) OR ({_MEMBERSHIP_SELF_PENDING})) "
                "WITH CHECK ("
                f"({_MEMBERSHIP_WRITE}) OR ({_MEMBERSHIP_SELF_ACTIVE})"
                ")"
            ),
            (
                "CREATE POLICY memberships_delete ON memberships FOR DELETE "
                f"USING (({_MEMBERSHIP_WRITE}) OR ({_MEMBERSHIP_SELF_PENDING}))"
            ),
        ]

    if table_name in _STAFF_TABLES:
        return [
            f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY",
            f"DROP POLICY IF EXISTS {policy_name(table_name)} ON {table_name}",
            *(
                f"DROP POLICY IF EXISTS {name} ON {table_name}"
                for name in staff_policy_names(table_name)
            ),
            (
                f"CREATE POLICY {table_name}_select ON {table_name} FOR SELECT "
                f"USING ({_STAFF_READ})"
            ),
            (
                f"CREATE POLICY {table_name}_insert ON {table_name} FOR INSERT "
                f"WITH CHECK ({_STAFF_WRITE})"
            ),
            (
                f"CREATE POLICY {table_name}_update ON {table_name} FOR UPDATE "
                f"USING ({_STAFF_WRITE}) WITH CHECK ({_STAFF_WRITE})"
            ),
            (
                f"CREATE POLICY {table_name}_delete ON {table_name} FOR DELETE "
                f"USING ({_STAFF_WRITE})"
            ),
        ]

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
    if table_name == "memberships":
        return [
            *(
                f"DROP POLICY IF EXISTS {name} ON memberships"
                for name in membership_policy_names()
            ),
            f"DROP POLICY IF EXISTS {policy_name('memberships')} ON memberships",
            "ALTER TABLE memberships NO FORCE ROW LEVEL SECURITY",
            "ALTER TABLE memberships DISABLE ROW LEVEL SECURITY",
        ]

    if table_name in _STAFF_TABLES:
        return [
            *(
                f"DROP POLICY IF EXISTS {name} ON {table_name}"
                for name in staff_policy_names(table_name)
            ),
            f"DROP POLICY IF EXISTS {policy_name(table_name)} ON {table_name}",
            f"ALTER TABLE {table_name} NO FORCE ROW LEVEL SECURITY",
            f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY",
        ]

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
