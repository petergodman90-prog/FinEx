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

    # Metrics that depend on upstream modules (orders/invoices) which are not
    # built yet. Listed here so the frontend can render placeholders.
    upstream_pending: list[str] = Field(
        default_factory=lambda: ["revenue_30d", "receivables_outstanding"]
    )
