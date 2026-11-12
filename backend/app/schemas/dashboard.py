"""Dashboard schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class RecentTouchpoint(BaseModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    customer_name: str
    kind: str
    summary: str
    occurred_at: datetime


class DashboardMetrics(BaseModel):
    org_id: uuid.UUID
    generated_at: datetime

    # Real numbers computed from tables that exist today.
    customers_total: int
    customers_new_30d: int
    touchpoints_30d: int
    proposals_pending: int
    proposals_pending_amount: Decimal | None = None
    recent_touchpoints: list[RecentTouchpoint] = Field(default_factory=list)

    # Ledger-derived totals. ``expenses_total`` includes cost of goods sold so
    # that revenue - expenses == net profit.
    currency: str = "USD"
    revenue_total: Decimal = Decimal("0")
    cogs_total: Decimal = Decimal("0")
    operating_expense_total: Decimal = Decimal("0")
    expenses_total: Decimal = Decimal("0")
    net_profit: Decimal = Decimal("0")
    orders_total: int = 0
    receivables_outstanding: Decimal = Decimal("0")
    payables_outstanding: Decimal = Decimal("0")
    overdue_receivables: Decimal = Decimal("0")
    overdue_payables: Decimal = Decimal("0")
    total_debtors: int = 0
    total_creditors: int = 0

    # Kept for older clients. Ledger-backed metrics are now included above.
    upstream_pending: list[str] = Field(default_factory=list)
