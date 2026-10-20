"""Unit tests for RBAC permissions and spend caps (no DB needed)."""

from __future__ import annotations

from decimal import Decimal

from app.core.rbac import (
    Role,
    check_permission,
    check_spend_cap,
)


def test_permission_matrix() -> None:
    assert check_permission("owner", "customers:read")
    assert check_permission("viewer", "customers:read")
    assert not check_permission("viewer", "customers:write")
    assert check_permission("staff", "customers:write")
    assert not check_permission("staff", "customers:delete")
    assert check_permission("manager", "customers:delete")

    assert not check_permission("staff", "proposals:approve")
    assert check_permission("manager", "proposals:approve")
    assert not check_permission("manager", "proposals:expire")
    assert check_permission("admin", "proposals:expire")


def test_unknown_role_or_permission_denied() -> None:
    assert not check_permission("intern", "customers:read")
    assert not check_permission("owner", "nonexistent:permission")


def test_spend_caps() -> None:
    assert check_spend_cap(Role.OWNER.value, Decimal("999999999"))
    assert check_spend_cap(Role.ADMIN.value, Decimal("1000000"))
    assert not check_spend_cap(Role.ADMIN.value, Decimal("1000000.01"))
    assert check_spend_cap(Role.MANAGER.value, Decimal("100000"))
    assert not check_spend_cap(Role.MANAGER.value, Decimal("100000.01"))
    assert check_spend_cap(Role.STAFF.value, Decimal("10000"))
    assert check_spend_cap(Role.VIEWER.value, Decimal("0"))
    assert not check_spend_cap(Role.VIEWER.value, Decimal("0.01"))
    assert not check_spend_cap("intern", Decimal("0"))
