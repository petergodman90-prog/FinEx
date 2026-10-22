"""Read models for the financial ledger.

Money, stock and balances are never recomputed in the browser and never
recomputed from business tables: they are read from the immutable journal
through the views the database maintains.  This module is the only place the
application reads ledger state from.

Every query is scoped by ``org_id`` in addition to the row level security
policy, so a read can never cross a tenant boundary even if the session
context were mis-bound.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.errors import NotFoundError

ZERO = Decimal("0")


# ── Row shapes ───────────────────────────────────────────────


@dataclass(frozen=True)
class Account:
    id: uuid.UUID
    code: str
    name: str
    account_type: str
    is_control: bool
    is_cash_account: bool
    is_active: bool


@dataclass(frozen=True)
class Product:
    id: uuid.UUID
    sku: str
    name: str
    unit: str
    current_sale_price: Decimal
    current_cost: Decimal
    reorder_point: Decimal
    is_active: bool


@dataclass(frozen=True)
class Supplier:
    id: uuid.UUID
    code: str | None
    name: str
    is_active: bool


@dataclass(frozen=True)
class StockLevel:
    product_id: uuid.UUID
    sku: str
    name: str
    quantity_on_hand: Decimal
    reorder_point: Decimal
    needs_reorder: bool


@dataclass(frozen=True)
class JournalEntry:
    id: uuid.UUID
    entry_number: int
    effective_at: datetime
    memo: str | None
    status: str
    reversal_of_id: uuid.UUID | None
    total_debit: Decimal
    total_credit: Decimal


@dataclass(frozen=True)
class JournalLine:
    line_number: int
    account_id: uuid.UUID
    account_code: str
    account_name: str
    account_type: str
    debit: Decimal
    credit: Decimal
    description: str | None


@dataclass(frozen=True)
class TrialBalanceRow:
    account_id: uuid.UUID
    account_code: str
    account_name: str
    account_type: str
    debit_total: Decimal
    credit_total: Decimal
    net_debit_balance: Decimal


@dataclass(frozen=True)
class OpenItem:
    id: uuid.UUID
    item_type: str
    counterparty_id: uuid.UUID
    original_amount: Decimal
    allocated_amount: Decimal
    outstanding_amount: Decimal
    due_on: date | None
    opened_at: datetime
    aging_bucket: str


@dataclass(frozen=True)
class Receipt:
    id: uuid.UUID
    receipt_number: int
    journal_entry_id: uuid.UUID
    currency_code: str
    customer_id: uuid.UUID | None
    total_amount: Decimal
    receipt_type: str
    credit_note_of_id: uuid.UUID | None
    issued_at: datetime
    notes: str | None


@dataclass(frozen=True)
class ReceiptLine:
    line_number: int
    description: str
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal


# ── Accounts ─────────────────────────────────────────────────

_ACCOUNT_COLUMNS = """
    id, code, name, account_type, is_control_account, is_cash_account, is_active
"""


def _account_from_row(row) -> Account:
    return Account(
        id=row.id,
        code=row.code,
        name=row.name,
        account_type=row.account_type,
        is_control=bool(row.is_control_account),
        is_cash_account=bool(row.is_cash_account),
        is_active=bool(row.is_active),
    )


async def list_accounts(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    include_inactive: bool = False,
    account_type: str | None = None,
) -> list[Account]:
    sql = f"SELECT {_ACCOUNT_COLUMNS} FROM ledger.accounts WHERE org_id = :org"
    params: dict = {"org": org_id}
    if not include_inactive:
        sql += " AND is_active"
    if account_type:
        sql += " AND account_type = CAST(:kind AS ledger.account_type)"
        params["kind"] = account_type
    sql += " ORDER BY code"
    rows = (await session.execute(text(sql), params)).all()
    return [_account_from_row(row) for row in rows]


async def get_account(
    session: AsyncSession, org_id: uuid.UUID, code: str
) -> Account | None:
    row = (
        await session.execute(
            text(
                f"SELECT {_ACCOUNT_COLUMNS} FROM ledger.accounts "
                "WHERE org_id = :org AND code = :code"
            ),
            {"org": org_id, "code": code},
        )
    ).one_or_none()
    return _account_from_row(row) if row else None


async def require_account(
    session: AsyncSession, org_id: uuid.UUID, code: str
) -> Account:
    account = await get_account(session, org_id, code)
    if account is None:
        raise NotFoundError(f"Account '{code}' is not set up for this organisation")
    if not account.is_active:
        raise NotFoundError(f"Account '{code}' is not active")
    return account


async def get_accounts_by_code(
    session: AsyncSession, org_id: uuid.UUID, codes: list[str]
) -> dict[str, Account]:
    if not codes:
        return {}
    rows = (
        await session.execute(
            text(
                f"SELECT {_ACCOUNT_COLUMNS} FROM ledger.accounts "
                "WHERE org_id = :org AND code = ANY(CAST(:codes AS text[]))"
            ),
            {"org": org_id, "codes": codes},
        )
    ).all()
    return {row.code: _account_from_row(row) for row in rows}


async def find_cash_account(
    session: AsyncSession, org_id: uuid.UUID, preferred_code: str | None = None
) -> Account:
    """The account money is held in.

    A preferred code wins when it exists; otherwise the organisation's own
    cash-flagged account is used.
    """
    if preferred_code:
        account = await get_account(session, org_id, preferred_code)
        if account is not None and account.is_active:
            return account
    row = (
        await session.execute(
            text(
                f"SELECT {_ACCOUNT_COLUMNS} FROM ledger.accounts "
                "WHERE org_id = :org AND is_cash_account AND is_active "
                "ORDER BY code LIMIT 1"
            ),
            {"org": org_id},
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError(
            "This organisation has no cash account. Set one up before posting."
        )
    return _account_from_row(row)


# ── Products ─────────────────────────────────────────────────

_PRODUCT_COLUMNS = """
    id, sku, name, unit, current_sale_price, current_cost, reorder_point, is_active
"""


def _product_from_row(row) -> Product:
    return Product(
        id=row.id,
        sku=row.sku,
        name=row.name,
        unit=row.unit,
        current_sale_price=row.current_sale_price,
        current_cost=row.current_cost,
        reorder_point=row.reorder_point,
        is_active=bool(row.is_active),
    )


async def get_product(
    session: AsyncSession, org_id: uuid.UUID, product_id: uuid.UUID
) -> Product | None:
    row = (
        await session.execute(
            text(
                f"SELECT {_PRODUCT_COLUMNS} FROM ledger.products "
                "WHERE org_id = :org AND id = CAST(:id AS uuid)"
            ),
            {"org": org_id, "id": product_id},
        )
    ).one_or_none()
    return _product_from_row(row) if row else None


async def get_products(
    session: AsyncSession, org_id: uuid.UUID, product_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Product]:
    if not product_ids:
        return {}
    rows = (
        await session.execute(
            text(
                f"SELECT {_PRODUCT_COLUMNS} FROM ledger.products "
                "WHERE org_id = :org AND id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"org": org_id, "ids": product_ids},
        )
    ).all()
    return {row.id: _product_from_row(row) for row in rows}


async def list_products(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    include_inactive: bool = False,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Product], int]:
    where = ["org_id = :org"]
    params: dict = {"org": org_id}
    if not include_inactive:
        where.append("is_active")
    if search:
        where.append("(sku ILIKE :q OR name ILIKE :q)")
        params["q"] = f"%{search.strip()}%"
    clause = " AND ".join(where)

    total = await session.scalar(
        text(f"SELECT count(*) FROM ledger.products WHERE {clause}"), params
    )
    rows = (
        await session.execute(
            text(
                f"SELECT {_PRODUCT_COLUMNS} FROM ledger.products WHERE {clause} "
                "ORDER BY sku LIMIT :limit OFFSET :offset"
            ),
            {**params, "limit": limit, "offset": offset},
        )
    ).all()
    return [_product_from_row(row) for row in rows], int(total or 0)


# ── Suppliers ────────────────────────────────────────────────


async def get_supplier(
    session: AsyncSession, org_id: uuid.UUID, supplier_id: uuid.UUID
) -> Supplier | None:
    row = (
        await session.execute(
            text(
                "SELECT id, code, name, is_active FROM ledger.suppliers "
                "WHERE org_id = :org AND id = CAST(:id AS uuid)"
            ),
            {"org": org_id, "id": supplier_id},
        )
    ).one_or_none()
    if row is None:
        return None
    return Supplier(
        id=row.id,
        code=row.code,
        name=row.name,
        is_active=bool(row.is_active),
    )


async def list_suppliers(
    session: AsyncSession, org_id: uuid.UUID, *, include_inactive: bool = False
) -> list[Supplier]:
    sql = "SELECT id, code, name, is_active FROM ledger.suppliers WHERE org_id = :org"
    if not include_inactive:
        sql += " AND is_active"
    sql += " ORDER BY name"
    rows = (await session.execute(text(sql), {"org": org_id})).all()
    return [
        Supplier(id=r.id, code=r.code, name=r.name, is_active=bool(r.is_active))
        for r in rows
    ]


# ── Stock ────────────────────────────────────────────────────


async def stock_quantity(
    session: AsyncSession, org_id: uuid.UUID, product_id: uuid.UUID
) -> Decimal:
    value = await session.scalar(
        text(
            "SELECT coalesce(sum(quantity_delta), 0) FROM ledger.stock_movements "
            "WHERE org_id = :org AND product_id = CAST(:id AS uuid)"
        ),
        {"org": org_id, "id": product_id},
    )
    return Decimal(value or 0)


async def list_stock_levels(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    only_needs_reorder: bool = False,
) -> list[StockLevel]:
    sql = """
        SELECT product_id, sku, name, quantity_on_hand, reorder_point, needs_reorder
        FROM ledger.v_inventory_on_hand
        WHERE org_id = :org
    """
    if only_needs_reorder:
        sql += " AND needs_reorder"
    sql += " ORDER BY sku"
    rows = (await session.execute(text(sql), {"org": org_id})).all()
    return [
        StockLevel(
            product_id=r.product_id,
            sku=r.sku,
            name=r.name,
            quantity_on_hand=Decimal(r.quantity_on_hand),
            reorder_point=Decimal(r.reorder_point),
            needs_reorder=bool(r.needs_reorder),
        )
        for r in rows
    ]


async def get_stock_level(
    session: AsyncSession, org_id: uuid.UUID, product_id: uuid.UUID
) -> StockLevel | None:
    row = (
        await session.execute(
            text(
                "SELECT product_id, sku, name, quantity_on_hand, reorder_point, "
                "needs_reorder FROM ledger.v_inventory_on_hand "
                "WHERE org_id = :org AND product_id = CAST(:id AS uuid)"
            ),
            {"org": org_id, "id": product_id},
        )
    ).one_or_none()
    if row is None:
        return None
    return StockLevel(
        product_id=row.product_id,
        sku=row.sku,
        name=row.name,
        quantity_on_hand=Decimal(row.quantity_on_hand),
        reorder_point=Decimal(row.reorder_point),
        needs_reorder=bool(row.needs_reorder),
    )


# ── Journal ──────────────────────────────────────────────────


async def list_journal_entries(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    limit: int = 50,
    offset: int = 0,
    since: datetime | None = None,
    until: datetime | None = None,
) -> tuple[list[JournalEntry], int]:
    where = ["je.org_id = :org"]
    params: dict = {"org": org_id}
    if since is not None:
        where.append("je.effective_at >= CAST(:since AS timestamptz)")
        params["since"] = since
    if until is not None:
        where.append("je.effective_at < CAST(:until AS timestamptz)")
        params["until"] = until
    clause = " AND ".join(where)

    total = await session.scalar(
        text(
            "SELECT count(*) FROM ledger.journal_entries je "
            f"WHERE {clause}"
        ),
        params,
    )
    rows = (
        await session.execute(
            text(
                """
                SELECT je.id, je.entry_number, je.effective_at, je.memo, je.status,
                       je.reversal_of_id,
                       coalesce((SELECT sum(jl.debit) FROM ledger.journal_lines jl
                                 WHERE jl.journal_entry_id = je.id), 0) AS total_debit,
                       coalesce((SELECT sum(jl.credit) FROM ledger.journal_lines jl
                                 WHERE jl.journal_entry_id = je.id), 0) AS total_credit
                FROM ledger.journal_entries je
                """
                f"WHERE {clause} "
                "ORDER BY je.entry_number DESC LIMIT :limit OFFSET :offset"
            ),
            {**params, "limit": limit, "offset": offset},
        )
    ).all()
    return (
        [
            JournalEntry(
                id=r.id,
                entry_number=r.entry_number,
                effective_at=r.effective_at,
                memo=r.memo,
                status=r.status,
                reversal_of_id=r.reversal_of_id,
                total_debit=Decimal(r.total_debit),
                total_credit=Decimal(r.total_credit),
            )
            for r in rows
        ],
        int(total or 0),
    )


async def get_journal_entry(
    session: AsyncSession, org_id: uuid.UUID, entry_id: uuid.UUID
) -> JournalEntry | None:
    row = (
        await session.execute(
            text(
                """
                SELECT je.id, je.entry_number, je.effective_at, je.memo, je.status,
                       je.reversal_of_id,
                       coalesce((SELECT sum(jl.debit) FROM ledger.journal_lines jl
                                 WHERE jl.journal_entry_id = je.id), 0) AS total_debit,
                       coalesce((SELECT sum(jl.credit) FROM ledger.journal_lines jl
                                 WHERE jl.journal_entry_id = je.id), 0) AS total_credit
                FROM ledger.journal_entries je
                WHERE je.org_id = :org AND je.id = CAST(:id AS uuid)
                """
            ),
            {"org": org_id, "id": entry_id},
        )
    ).one_or_none()
    if row is None:
        return None
    return JournalEntry(
        id=row.id,
        entry_number=row.entry_number,
        effective_at=row.effective_at,
        memo=row.memo,
        status=row.status,
        reversal_of_id=row.reversal_of_id,
        total_debit=Decimal(row.total_debit),
        total_credit=Decimal(row.total_credit),
    )


async def get_journal_lines(
    session: AsyncSession, org_id: uuid.UUID, entry_id: uuid.UUID
) -> list[JournalLine]:
    rows = (
        await session.execute(
            text(
                """
                SELECT jl.line_number, jl.account_id, a.code AS account_code,
                       a.name AS account_name, a.account_type,
                       jl.debit, jl.credit, jl.description
                FROM ledger.journal_lines jl
                JOIN ledger.accounts a ON a.id = jl.account_id AND a.org_id = jl.org_id
                WHERE jl.org_id = :org AND jl.journal_entry_id = CAST(:id AS uuid)
                ORDER BY jl.line_number
                """
            ),
            {"org": org_id, "id": entry_id},
        )
    ).all()
    return [
        JournalLine(
            line_number=r.line_number,
            account_id=r.account_id,
            account_code=r.account_code,
            account_name=r.account_name,
            account_type=r.account_type,
            debit=Decimal(r.debit),
            credit=Decimal(r.credit),
            description=r.description,
        )
        for r in rows
    ]


async def trial_balance(
    session: AsyncSession, org_id: uuid.UUID
) -> list[TrialBalanceRow]:
    rows = (
        await session.execute(
            text(
                """
                SELECT account_id, account_code, account_name, account_type,
                       debit_total, credit_total, net_debit_balance
                FROM ledger.v_trial_balance
                WHERE org_id = :org
                ORDER BY account_code
                """
            ),
            {"org": org_id},
        )
    ).all()
    return [
        TrialBalanceRow(
            account_id=r.account_id,
            account_code=r.account_code,
            account_name=r.account_name,
            account_type=r.account_type,
            debit_total=Decimal(r.debit_total),
            credit_total=Decimal(r.credit_total),
            net_debit_balance=Decimal(r.net_debit_balance),
        )
        for r in rows
    ]


# ── Open items ───────────────────────────────────────────────


async def list_open_items(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    item_type: str | None = None,
    outstanding_only: bool = True,
    counterparty_id: uuid.UUID | None = None,
) -> list[OpenItem]:
    sql = "SELECT * FROM ledger.v_open_item_balances WHERE org_id = :org"
    params: dict = {"org": org_id}
    if item_type:
        sql += " AND item_type = CAST(:kind AS ledger.open_item_type)"
        params["kind"] = item_type
    if outstanding_only:
        sql += " AND outstanding_amount <> 0"
    if counterparty_id is not None:
        sql += " AND counterparty_id = CAST(:cp AS uuid)"
        params["cp"] = counterparty_id
    sql += " ORDER BY item_type, opened_at"
    rows = (await session.execute(text(sql), params)).all()
    return [
        OpenItem(
            id=r.open_item_id,
            item_type=r.item_type,
            counterparty_id=r.counterparty_id,
            original_amount=Decimal(r.original_amount),
            allocated_amount=Decimal(r.allocated_amount),
            outstanding_amount=Decimal(r.outstanding_amount),
            due_on=r.due_on,
            opened_at=r.opened_at,
            aging_bucket=r.aging_bucket,
        )
        for r in rows
    ]


async def get_open_item(
    session: AsyncSession, org_id: uuid.UUID, open_item_id: uuid.UUID
) -> OpenItem | None:
    row = (
        await session.execute(
            text(
                """
                SELECT open_item_id, item_type, counterparty_id, original_amount,
                       allocated_amount, outstanding_amount, due_on, opened_at,
                       aging_bucket
                FROM ledger.v_open_item_balances
                WHERE org_id = :org AND open_item_id = CAST(:id AS uuid)
                """
            ),
            {"org": org_id, "id": open_item_id},
        )
    ).one_or_none()
    if row is None:
        return None
    return OpenItem(
        id=row.open_item_id,
        item_type=row.item_type,
        counterparty_id=row.counterparty_id,
        original_amount=Decimal(row.original_amount),
        allocated_amount=Decimal(row.allocated_amount),
        outstanding_amount=Decimal(row.outstanding_amount),
        due_on=row.due_on,
        opened_at=row.opened_at,
        aging_bucket=row.aging_bucket,
    )


# ── Receipts ─────────────────────────────────────────────────


async def list_receipts(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    receipt_type: str | None = None,
    customer_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Receipt], int]:
    where = ["org_id = :org"]
    params: dict = {"org": org_id}
    if receipt_type:
        where.append("receipt_type = CAST(:kind AS ledger.receipt_type)")
        params["kind"] = receipt_type
    if customer_id is not None:
        where.append("customer_id = CAST(:cust AS uuid)")
        params["cust"] = customer_id
    clause = " AND ".join(where)

    total = await session.scalar(
        text(f"SELECT count(*) FROM ledger.receipts WHERE {clause}"), params
    )
    rows = (
        await session.execute(
            text(
                """
                SELECT id, receipt_number, journal_entry_id, currency_code,
                       customer_id, total_amount, receipt_type, credit_note_of_id,
                       issued_at, notes
                FROM ledger.receipts
                """
                f"WHERE {clause} "
                "ORDER BY receipt_number DESC LIMIT :limit OFFSET :offset"
            ),
            {**params, "limit": limit, "offset": offset},
        )
    ).all()
    return (
        [
            Receipt(
                id=r.id,
                receipt_number=r.receipt_number,
                journal_entry_id=r.journal_entry_id,
                currency_code=r.currency_code,
                customer_id=r.customer_id,
                total_amount=Decimal(r.total_amount),
                receipt_type=r.receipt_type,
                credit_note_of_id=r.credit_note_of_id,
                issued_at=r.issued_at,
                notes=r.notes,
            )
            for r in rows
        ],
        int(total or 0),
    )


async def get_receipt(
    session: AsyncSession, org_id: uuid.UUID, receipt_id: uuid.UUID
) -> Receipt | None:
    row = (
        await session.execute(
            text(
                """
                SELECT id, receipt_number, journal_entry_id, currency_code,
                       customer_id, total_amount, receipt_type, credit_note_of_id,
                       issued_at, notes
                FROM ledger.receipts
                WHERE org_id = :org AND id = CAST(:id AS uuid)
                """
            ),
            {"org": org_id, "id": receipt_id},
        )
    ).one_or_none()
    if row is None:
        return None
    return Receipt(
        id=row.id,
        receipt_number=row.receipt_number,
        journal_entry_id=row.journal_entry_id,
        currency_code=row.currency_code,
        customer_id=row.customer_id,
        total_amount=Decimal(row.total_amount),
        receipt_type=row.receipt_type,
        credit_note_of_id=row.credit_note_of_id,
        issued_at=row.issued_at,
        notes=row.notes,
    )


async def get_receipt_lines(
    session: AsyncSession, receipt_id: uuid.UUID
) -> list[ReceiptLine]:
    rows = (
        await session.execute(
            text(
                """
                SELECT line_number, description, quantity, unit_price, line_total
                FROM ledger.receipt_lines
                WHERE receipt_id = CAST(:id AS uuid)
                ORDER BY line_number
                """
            ),
            {"id": receipt_id},
        )
    ).all()
    return [
        ReceiptLine(
            line_number=r.line_number,
            description=r.description,
            quantity=Decimal(r.quantity),
            unit_price=Decimal(r.unit_price),
            line_total=Decimal(r.line_total),
        )
        for r in rows
    ]


# ── Accounting periods ───────────────────────────────────────


async def list_accounting_periods(
    session: AsyncSession, org_id: uuid.UUID
) -> list[dict]:
    rows = (
        await session.execute(
            text(
                """
                SELECT id, period_start, period_end, is_closed, closed_at
                FROM ledger.accounting_periods
                WHERE org_id = :org
                ORDER BY period_start
                """
            ),
            {"org": org_id},
        )
    ).all()
    return [
        {
            "id": r.id,
            "period_start": r.period_start,
            "period_end": r.period_end,
            "is_closed": bool(r.is_closed),
            "closed_at": r.closed_at,
        }
        for r in rows
    ]


async def find_closed_period(
    session: AsyncSession, org_id: uuid.UUID, effective_at: datetime
) -> dict | None:
    """The closed period that contains ``effective_at``, if any."""
    row = (
        await session.execute(
            text(
                """
                SELECT id, period_start, period_end
                FROM ledger.accounting_periods
                WHERE org_id = :org AND is_closed
                  AND CAST(:eff AS date) BETWEEN period_start AND period_end
                LIMIT 1
                """
            ),
            {"org": org_id, "eff": effective_at},
        )
    ).one_or_none()
    if row is None:
        return None
    return {
        "id": row.id,
        "period_start": row.period_start,
        "period_end": row.period_end,
    }
