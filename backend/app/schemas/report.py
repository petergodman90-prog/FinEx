"""Financial report read models."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class ReportLineOut(BaseModel):
    account_id: uuid.UUID
    account_code: str
    account_name: str
    account_type: str
    debit_total: Decimal
    credit_total: Decimal
    amount: Decimal


class ProfitAndLossOut(BaseModel):
    period_start: date
    period_end: date
    revenue_total: Decimal
    cogs_total: Decimal
    gross_profit: Decimal
    operating_expense_total: Decimal
    net_profit: Decimal
    revenue_lines: list[ReportLineOut]
    cogs_lines: list[ReportLineOut]
    operating_expense_lines: list[ReportLineOut]


class BalanceSheetOut(BaseModel):
    as_of: date
    asset_lines: list[ReportLineOut]
    liability_lines: list[ReportLineOut]
    equity_lines: list[ReportLineOut]
    total_assets: Decimal
    total_liabilities: Decimal
    total_equity: Decimal
    current_earnings: Decimal
    total_liabilities_and_equity: Decimal
    is_balanced: bool


class TrialBalanceRowOut(BaseModel):
    account_id: uuid.UUID
    account_code: str
    account_name: str
    account_type: str
    debit_total: Decimal
    credit_total: Decimal
    net_debit_balance: Decimal


class TrialBalanceOut(BaseModel):
    rows: list[TrialBalanceRowOut]
    total_debits: Decimal
    total_credits: Decimal
    is_balanced: bool
