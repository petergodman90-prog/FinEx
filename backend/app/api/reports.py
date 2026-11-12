"""Ledger-derived financial report API."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TokenPayload
from app.core.dependencies import (
    get_current_user,
    get_rls_session,
    require_permission,
)
from app.schemas.report import (
    BalanceSheetOut,
    ProfitAndLossOut,
    ReportLineOut,
    TrialBalanceOut,
    TrialBalanceRowOut,
)
from app.services import ledger_service
from app.services.errors import DomainValidationError

router = APIRouter(prefix="/reports", tags=["reports"])


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _resolve_period(
    start: date | None, end: date | None
) -> tuple[date, date]:
    period_end = end or _today()
    period_start = start or period_end.replace(day=1)
    if period_start > period_end:
        raise DomainValidationError(
            "Report start date must not be after its end date",
            details={
                "start": period_start.isoformat(),
                "end": period_end.isoformat(),
            },
        )
    return period_start, period_end


def _line_out(line: ledger_service.ReportLine) -> ReportLineOut:
    return ReportLineOut(
        account_id=line.account_id,
        account_code=line.account_code,
        account_name=line.account_name,
        account_type=line.account_type,
        debit_total=line.debit_total,
        credit_total=line.credit_total,
        amount=line.amount,
    )


def _profit_and_loss_out(
    report: ledger_service.ProfitAndLossReport,
) -> ProfitAndLossOut:
    return ProfitAndLossOut(
        period_start=report.period_start,
        period_end=report.period_end,
        revenue_total=report.revenue_total,
        cogs_total=report.cogs_total,
        gross_profit=report.gross_profit,
        operating_expense_total=report.operating_expense_total,
        net_profit=report.net_profit,
        revenue_lines=[_line_out(line) for line in report.revenue_lines],
        cogs_lines=[_line_out(line) for line in report.cogs_lines],
        operating_expense_lines=[
            _line_out(line) for line in report.operating_expense_lines
        ],
    )


def _balance_sheet_out(
    report: ledger_service.BalanceSheetReport,
) -> BalanceSheetOut:
    return BalanceSheetOut(
        as_of=report.as_of,
        asset_lines=[_line_out(line) for line in report.asset_lines],
        liability_lines=[_line_out(line) for line in report.liability_lines],
        equity_lines=[_line_out(line) for line in report.equity_lines],
        total_assets=report.total_assets,
        total_liabilities=report.total_liabilities,
        total_equity=report.total_equity,
        current_earnings=report.current_earnings,
        total_liabilities_and_equity=report.total_liabilities_and_equity,
        is_balanced=report.is_balanced,
    )


@router.get(
    "/profit-and-loss",
    response_model=ProfitAndLossOut,
    dependencies=[Depends(require_permission("reports:read"))],
)
async def get_profit_and_loss(
    start: date | None = Query(
        None, description="Inclusive period start (defaults to month start)"
    ),
    end: date | None = Query(
        None, description="Inclusive period end (defaults to today)"
    ),
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> ProfitAndLossOut:
    period_start, period_end = _resolve_period(start, end)
    report = await ledger_service.profit_and_loss(
        session,
        user.org_id,
        start=period_start,
        end=period_end,
    )
    return _profit_and_loss_out(report)


@router.get(
    "/balance-sheet",
    response_model=BalanceSheetOut,
    dependencies=[Depends(require_permission("reports:read"))],
)
async def get_balance_sheet(
    as_of: date | None = Query(
        None, description="Balance-sheet date (defaults to today)"
    ),
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> BalanceSheetOut:
    report = await ledger_service.balance_sheet(
        session,
        user.org_id,
        as_of=as_of or _today(),
    )
    return _balance_sheet_out(report)


@router.get(
    "/trial-balance",
    response_model=TrialBalanceOut,
    dependencies=[Depends(require_permission("reports:read"))],
)
async def get_trial_balance(
    user: TokenPayload = Depends(get_current_user),
    session: AsyncSession = Depends(get_rls_session),
) -> TrialBalanceOut:
    rows = await ledger_service.trial_balance(session, user.org_id)
    total_debits = sum(
        (row.debit_total for row in rows),
        Decimal("0"),
    )
    total_credits = sum(
        (row.credit_total for row in rows),
        Decimal("0"),
    )
    return TrialBalanceOut(
        rows=[
            TrialBalanceRowOut(
                account_id=row.account_id,
                account_code=row.account_code,
                account_name=row.account_name,
                account_type=row.account_type,
                debit_total=row.debit_total,
                credit_total=row.credit_total,
                net_debit_balance=row.net_debit_balance,
            )
            for row in rows
        ],
        total_debits=total_debits,
        total_credits=total_credits,
        is_balanced=total_debits == total_credits,
    )
