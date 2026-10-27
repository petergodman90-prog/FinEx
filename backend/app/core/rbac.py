"""
Role-Based Access Control — data definitions and pure checks.

Roles: owner > admin > manager > staff > viewer
Permissions: resource:action strings mapped to allowed roles.
Spend caps: per-role limits for proposal approval.

This module is intentionally free of FastAPI or database imports so
it can be used everywhere without circular-dependency issues.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum


class Role(StrEnum):
    """Application roles, ordered by privilege level."""

    OWNER = "owner"
    ADMIN = "admin"
    MANAGER = "manager"
    STAFF = "staff"
    VIEWER = "viewer"


# Higher number → more privileged
ROLE_WEIGHT: dict[Role, int] = {
    Role.OWNER: 50,
    Role.ADMIN: 40,
    Role.MANAGER: 30,
    Role.STAFF: 20,
    Role.VIEWER: 10,
}

# ── Permission Matrix ──────────────────────────────────────
# Key: "resource:action"   Value: set of roles that have it

PERMISSIONS: dict[str, set[Role]] = {
    # CRM
    "customers:read": {Role.VIEWER, Role.STAFF, Role.MANAGER, Role.ADMIN, Role.OWNER},
    "customers:write": {Role.STAFF, Role.MANAGER, Role.ADMIN, Role.OWNER},
    "customers:delete": {Role.MANAGER, Role.ADMIN, Role.OWNER},
    # Proposals
    "proposals:read": {Role.VIEWER, Role.STAFF, Role.MANAGER, Role.ADMIN, Role.OWNER},
    "proposals:create": {Role.STAFF, Role.MANAGER, Role.ADMIN, Role.OWNER},
    "proposals:approve": {Role.MANAGER, Role.ADMIN, Role.OWNER},
    "proposals:expire": {Role.ADMIN, Role.OWNER},
    # Dashboard
    "dashboard:read": {Role.VIEWER, Role.STAFF, Role.MANAGER, Role.ADMIN, Role.OWNER},
    # Staff management
    "staff:read": {Role.MANAGER, Role.ADMIN, Role.OWNER},
    "staff:manage": {Role.ADMIN, Role.OWNER},
    # Organization
    "org:settings": {Role.OWNER},
    "org:export": {Role.ADMIN, Role.OWNER},
    # Audit trail
    "audit:read": {Role.ADMIN, Role.OWNER},
}

# ── Spend Caps ─────────────────────────────────────────────
# Maximum spend a role can approve in a single proposal.
# None → unlimited.

SPEND_CAPS: dict[Role, Decimal | None] = {
    Role.OWNER: None,
    Role.ADMIN: Decimal("1_000_000"),
    Role.MANAGER: Decimal("100_000"),
    Role.STAFF: Decimal("10_000"),
    Role.VIEWER: Decimal("0"),
}


# ── Pure Check Functions ───────────────────────────────────


def check_permission(role: str, permission: str) -> bool:
    """Return True if *role* has *permission*."""
    allowed = PERMISSIONS.get(permission)
    if allowed is None:
        return False
    try:
        return Role(role) in allowed
    except ValueError:
        return False


def check_spend_cap(role: str, amount: Decimal) -> bool:
    """Return True if *role* can approve *amount*."""
    try:
        cap = SPEND_CAPS[Role(role)]
    except (ValueError, KeyError):
        return False
    if cap is None:  # unlimited
        return True
    return amount <= cap
