"""Person A posting service — the only application write path into the ledger.

Every financial or stock change goes through the SECURITY DEFINER functions in
the ``ledger`` schema.  This service binds the organisation context, validates
cross-domain references, calls those functions in one database transaction, and
returns the journal-entry id that the approval endpoint stores on the proposal.

Design rules:

* **One transaction.** The caller owns the transaction (Person B already opens
  one in ``get_rls_session``).  This service uses a savepoint so a failed post
  rolls back all financial writes while leaving the caller able to record a
  retryable failure on the proposal and commit that state.
* **Idempotent.** ``idempotency_key`` is always the proposal id for approved
  proposals.  A transaction-scoped advisory lock serialises concurrent
  approvals; the database's posting-event key is the final guard.  A retry
  returns the original journal entry rather than posting twice.
* **Tenant-safe.** The session org is bound and compared with the requested org
  before any privileged function is invoked.  Customer, product, supplier and
  account references are validated in the same organisation.
* **No partial financial writes.** Stock, receipt, open-item and journal rows
  either all commit or all roll back with the savepoint.

Manual proposals are intentionally *not* postable.  They are informational and
have no financial payload; the UI must keep them out of the ledger.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rls import bind_session_context
from app.services import ledger_service

# Default chart codes seeded by ledger.seed_default_chart_of_accounts().
CASH = "1000"
RECEIVABLES = "1100"
INVENTORY = "1200"
PAYABLES = "2000"
OPENING_EQUITY = "3000"
SALES_REVENUE = "4000"
COGS = "5000"
OPERATING_EXPENSES = "6000"

EXPENSE_CATEGORY_ACCOUNTS = {
    "other": OPERATING_EXPENSES,
    "operating": OPERATING_EXPENSES,
    "rent": "6100",
    "utilities": "6100",
    "salaries": "6200",
    "payroll": "6200",
    "wages": "6200",
    "marketing": "6300",
    "advertising": "6300",
    "bad_debt": "6400",
    "bank_charges": "6500",
    "inventory_variance": "6600",
}

MONEY_QUANT = Decimal("0.0001")


# ── Errors ───────────────────────────────────────────────────


class PostingError(Exception):
    """A posting failed. ``retryable`` tells the approval flow what to show."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        retryable: bool,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.details = details or {}

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "message": str(self),
            "retryable": self.retryable,
            "details": self.details,
        }


class PostingValidationError(PostingError):
    def __init__(self, message: str, *, code: str = "invalid_payload", details: dict | None = None):
        super().__init__(message, code=code, retryable=False, details=details)


class PostingConflictError(PostingError):
    def __init__(self, message: str, *, code: str = "posting_conflict", details: dict | None = None):
        super().__init__(message, code=code, retryable=False, details=details)


class PostingRetryableError(PostingError):
    def __init__(self, message: str, *, code: str = "posting_failed", details: dict | None = None):
        super().__init__(message, code=code, retryable=True, details=details)


# ── Payload models ───────────────────────────────────────────


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _upper_currency(value: str) -> str:
    return value.strip().upper()


class SaleLineInput(_Payload):
    product_id: uuid.UUID
    quantity: Decimal = Field(gt=0, max_digits=19, decimal_places=4)
    unit_price: Decimal = Field(ge=0, max_digits=19, decimal_places=4)
    unit_cost: Decimal = Field(ge=0, max_digits=19, decimal_places=4)
    description: str | None = Field(default=None, max_length=500)


class SalePaymentInput(_Payload):
    method: Literal["cash", "receivable"] = "cash"
    cash_account_code: str = CASH
    due_on: date | None = None
    issue_receipt: bool | None = None
    receipt_notes: str | None = Field(default=None, max_length=2_000)


class SalePayload(_Payload):
    customer_id: uuid.UUID
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")
    effective_at: datetime
    lines: list[SaleLineInput] = Field(min_length=1)
    payment: SalePaymentInput = Field(default_factory=SalePaymentInput)
    memo: str | None = Field(default=None, max_length=2_000)

    _normalise_currency = field_validator("currency", mode="before")(_upper_currency)
    _normalise_effective_at = field_validator("effective_at")(_aware_utc)


class ExpensePaymentInput(_Payload):
    method: Literal["cash", "payable"] = "cash"
    cash_account_code: str = CASH
    supplier_id: uuid.UUID | None = None
    due_on: date | None = None


class ExpensePayload(_Payload):
    amount: Decimal = Field(gt=0, max_digits=19, decimal_places=4)
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")
    effective_at: datetime
    expense_account_code: str | None = None
    category: str | None = Field(default=None, max_length=50)
    vendor: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2_000)
    payment: ExpensePaymentInput = Field(default_factory=ExpensePaymentInput)

    _normalise_currency = field_validator("currency", mode="before")(_upper_currency)
    _normalise_effective_at = field_validator("effective_at")(_aware_utc)


class OpeningStockLineInput(_Payload):
    product_id: uuid.UUID
    quantity: Decimal = Field(gt=0, max_digits=19, decimal_places=4)
    unit_cost: Decimal = Field(ge=0, max_digits=19, decimal_places=4)
    description: str | None = Field(default=None, max_length=500)


class OpeningStockPayload(_Payload):
    effective_at: datetime
    lines: list[OpeningStockLineInput] = Field(min_length=1)
    memo: str | None = Field(default=None, max_length=2_000)

    _normalise_effective_at = field_validator("effective_at")(_aware_utc)


class CreditNoteLineInput(_Payload):
    product_id: uuid.UUID | None = None
    quantity: Decimal = Field(gt=0, max_digits=19, decimal_places=4)
    unit_price: Decimal = Field(ge=0, max_digits=19, decimal_places=4)
    unit_cost: Decimal | None = Field(default=None, ge=0, max_digits=19, decimal_places=4)
    description: str | None = Field(default=None, max_length=500)


class CreditNotePayload(_Payload):
    customer_id: uuid.UUID | None = None
    currency: str | None = Field(
        default=None, min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$"
    )
    effective_at: datetime
    lines: list[CreditNoteLineInput] = Field(min_length=1)
    original_receipt_id: uuid.UUID | None = None
    restock: bool = True
    refund_method: Literal["cash"] = "cash"
    cash_account_code: str = CASH
    notes: str | None = Field(default=None, max_length=2_000)
    memo: str | None = Field(default=None, max_length=2_000)

    _normalise_currency = field_validator("currency", mode="before")(
        lambda value: _upper_currency(value) if value is not None else None
    )
    _normalise_effective_at = field_validator("effective_at")(_aware_utc)


# ── Result ───────────────────────────────────────────────────


@dataclass(frozen=True)
class PostingResult:
    journal_entry_id: uuid.UUID
    replayed: bool = False
    receipt_id: uuid.UUID | None = None
    credit_note_id: uuid.UUID | None = None
    open_item_id: uuid.UUID | None = None
    allocation_id: uuid.UUID | None = None
    stock_movement_ids: tuple[uuid.UUID, ...] = field(default_factory=tuple)


# ── Small helpers ────────────────────────────────────────────


def _quantize(value: Decimal | int | str) -> Decimal:
    try:
        return Decimal(value).quantize(MONEY_QUANT)
    except (InvalidOperation, ValueError) as exc:
        raise PostingValidationError("Invalid monetary amount", code="invalid_amount") from exc


def _parse(model: type[_Payload], payload: dict | None, label: str) -> Any:
    try:
        return model.model_validate(payload or {})
    except ValidationError as exc:
        errors = [
            {
                "field": ".".join(str(part) for part in error["loc"]),
                "message": error["msg"],
                "type": error["type"],
            }
            for error in exc.errors()
        ]
        raise PostingValidationError(
            f"Invalid {label} payload", code="invalid_payload", details={"errors": errors}
        ) from exc


def _line(
    account_id: uuid.UUID, *, debit: Decimal = Decimal("0"), credit: Decimal = Decimal("0"), description: str | None = None
) -> dict:
    return {
        "account_id": str(account_id),
        "debit": str(debit),
        "credit": str(credit),
        "description": description or "",
    }


def _json(value: Any) -> str:
    return json.dumps(value, default=str)


def _db_error(exc: DBAPIError) -> PostingError:
    """Translate a database posting error into a retryable/non-retryable error."""
    original = getattr(exc, "orig", None) or exc
    state = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    message = str(original) or str(exc)
    lowered = message.lower()

    if state == "42501" or "organisation context mismatch" in lowered:
        return PostingConflictError(
            "The posting belongs to another organisation",
            code="cross_org",
            details={"sqlstate": state},
        )
    if "insufficient stock" in lowered:
        return PostingRetryableError(
            "Insufficient stock for one or more products",
            code="insufficient_stock",
            details={"sqlstate": state},
        )
    if "closed accounting period" in lowered:
        return PostingRetryableError(
            "The effective date falls in a closed accounting period",
            code="closed_period",
            details={"sqlstate": state},
        )
    if "unbalanced" in lowered:
        return PostingValidationError(
            "The journal entry is unbalanced", code="unbalanced_entry"
        )
    if "period end must not precede" in lowered:
        return PostingValidationError(
            "The period end must not precede its start", code="invalid_period"
        )
    if "accounting period was not found" in lowered:
        return PostingValidationError(
            "The accounting period does not exist in this organisation",
            code="accounting_period_not_found",
        )
    if "exceeds the outstanding balance" in lowered:
        return PostingValidationError(
            "The settlement exceeds the outstanding balance",
            code="invalid_settlement_amount",
        )
    if "already been reversed" in lowered:
        return PostingConflictError(
            "The journal entry has already been reversed", code="already_reversed"
        )
    if state == "23505":
        return PostingConflictError(
            "The posting conflicts with an existing record", code="duplicate_posting"
        )
    return PostingRetryableError(
        "The posting could not be completed; retry is safe",
        code="posting_failed",
        details={"sqlstate": state},
    )


async def _bind_org(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    user_id: uuid.UUID | None = None,
    role: str | None = None,
) -> None:
    raw = await session.scalar(
        text("SELECT nullif(current_setting('app.current_org_id', true), '')")
    )
    if raw:
        try:
            current = uuid.UUID(str(raw))
        except ValueError as exc:
            raise PostingConflictError(
                "The session organisation context is invalid", code="invalid_org_context"
            ) from exc
        if current != org_id:
            raise PostingConflictError(
                "The session is bound to a different organisation", code="cross_org"
            )
        return
    await bind_session_context(session, org_id=org_id, user_id=user_id, role=role)


async def _lock_idempotency_key(
    session: AsyncSession, org_id: uuid.UUID, idempotency_key: uuid.UUID
) -> None:
    # Transaction-scoped lock: concurrent approval retries queue here and the
    # second one sees the first one's committed posting event.
    await session.execute(
        text(
            "SELECT pg_advisory_xact_lock("
            "hashtextextended(CAST(:key AS text), 0))"
        ),
        {"key": f"finex-posting:{org_id}:{idempotency_key}"},
    )


async def _existing_entry(
    session: AsyncSession, org_id: uuid.UUID, idempotency_key: uuid.UUID
) -> PostingResult | None:
    row = (
        await session.execute(
            text(
                """
                SELECT je.id AS journal_entry_id,
                       (
                           SELECT r.id
                           FROM ledger.receipts r
                           WHERE r.org_id = pe.org_id
                             AND r.journal_entry_id = je.id
                             AND r.receipt_type = 'receipt'
                           ORDER BY r.receipt_number
                           LIMIT 1
                       ) AS receipt_id,
                       (
                           SELECT r.id
                           FROM ledger.receipts r
                           WHERE r.org_id = pe.org_id
                             AND r.journal_entry_id = je.id
                             AND r.receipt_type = 'credit_note'
                           ORDER BY r.receipt_number
                           LIMIT 1
                       ) AS credit_note_id,
                       (
                           SELECT oi.id
                           FROM ledger.open_items oi
                           WHERE oi.org_id = pe.org_id
                             AND oi.originating_journal_entry_id = je.id
                           ORDER BY oi.created_at
                           LIMIT 1
                       ) AS originating_open_item_id,
                       (
                           SELECT pa.id
                           FROM ledger.payment_allocations pa
                           WHERE pa.org_id = pe.org_id
                             AND pa.payment_journal_entry_id = je.id
                           ORDER BY pa.created_at
                           LIMIT 1
                       ) AS allocation_id,
                       (
                           SELECT pa.open_item_id
                           FROM ledger.payment_allocations pa
                           WHERE pa.org_id = pe.org_id
                             AND pa.payment_journal_entry_id = je.id
                           ORDER BY pa.created_at
                           LIMIT 1
                       ) AS settled_open_item_id
                FROM ledger.posting_events pe
                JOIN ledger.journal_entries je ON je.posting_event_id = pe.id
                WHERE pe.org_id = CAST(:org AS uuid)
                  AND pe.idempotency_key = CAST(:key AS uuid)
                """
            ),
            {"org": org_id, "key": idempotency_key},
        )
    ).one_or_none()
    if row is None:
        return None
    return PostingResult(
        journal_entry_id=row.journal_entry_id,
        replayed=True,
        receipt_id=row.receipt_id,
        credit_note_id=row.credit_note_id,
        open_item_id=row.settled_open_item_id or row.originating_open_item_id,
        allocation_id=row.allocation_id,
    )


async def _run(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    idempotency_key: uuid.UUID,
    operation,
    user_id: uuid.UUID | None = None,
    role: str | None = None,
) -> PostingResult:
    await _bind_org(session, org_id, user_id=user_id, role=role)
    await _lock_idempotency_key(session, org_id, idempotency_key)

    existing = await _existing_entry(session, org_id, idempotency_key)
    if existing is not None:
        return existing

    try:
        # Savepoint: financial writes vanish on failure, but the caller's
        # proposal status/error update can still be committed.
        async with session.begin_nested():
            return await operation()
    except PostingError:
        raise
    except DBAPIError as exc:
        raise _db_error(exc) from exc


async def _run_setup(session: AsyncSession, org_id: uuid.UUID, statement, params: dict, *, user_id=None):
    await _bind_org(session, org_id, user_id=user_id)
    try:
        async with session.begin_nested():
            return await session.scalar(statement, params)
    except DBAPIError as exc:
        raise _db_error(exc) from exc


# ── Finance setup ────────────────────────────────────────────


async def ensure_default_chart(
    session: AsyncSession, org_id: uuid.UUID
) -> dict[str, ledger_service.Account]:
    """Idempotently create the standard chart and return it keyed by code."""
    await _bind_org(session, org_id)
    try:
        async with session.begin_nested():
            await session.execute(
                text("SELECT ledger.seed_default_chart_of_accounts(CAST(:org AS uuid))"),
                {"org": org_id},
            )
    except DBAPIError as exc:
        raise _db_error(exc) from exc
    accounts = await ledger_service.list_accounts(
        session, org_id, include_inactive=True
    )
    return {account.code: account for account in accounts}


async def create_account(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    code: str,
    name: str,
    account_type: Literal["asset", "liability", "equity", "revenue", "expense", "cogs"],
    is_control_account: bool = False,
    is_cash_account: bool = False,
    user_id: uuid.UUID | None = None,
) -> uuid.UUID:
    return await _run_setup(
        session,
        org_id,
        text(
            """
            SELECT ledger.create_account(
                CAST(:org AS uuid), :code, :name,
                CAST(:kind AS ledger.account_type),
                :control, :cash
            )
            """
        ),
        {
            "org": org_id,
            "code": code,
            "name": name,
            "kind": account_type,
            "control": is_control_account,
            "cash": is_cash_account,
        },
        user_id=user_id,
    )


async def update_account(
    session: AsyncSession,
    org_id: uuid.UUID,
    account_id: uuid.UUID,
    *,
    name: str | None = None,
    is_active: bool | None = None,
    is_cash_account: bool | None = None,
    user_id: uuid.UUID | None = None,
) -> uuid.UUID:
    return await _run_setup(
        session,
        org_id,
        text(
            """
            SELECT ledger.update_account(
                CAST(:org AS uuid), CAST(:account AS uuid),
                :name, :active, :cash
            )
            """
        ),
        {
            "org": org_id,
            "account": account_id,
            "name": name,
            "active": is_active,
            "cash": is_cash_account,
        },
        user_id=user_id,
    )


async def create_product(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    sku: str,
    name: str,
    unit: str = "each",
    sale_price: Decimal | str,
    cost: Decimal | str = Decimal("0"),
    reorder_point: Decimal | str = Decimal("0"),
    user_id: uuid.UUID | None = None,
) -> uuid.UUID:
    return await _run_setup(
        session,
        org_id,
        text(
            """
            SELECT ledger.create_product(
                CAST(:org AS uuid), :sku, :name, :unit,
                CAST(:price AS numeric), CAST(:cost AS numeric),
                CAST(:reorder AS numeric)
            )
            """
        ),
        {
            "org": org_id,
            "sku": sku,
            "name": name,
            "unit": unit,
            "price": _quantize(sale_price),
            "cost": _quantize(cost),
            "reorder": _quantize(reorder_point),
        },
        user_id=user_id,
    )


async def update_product(
    session: AsyncSession,
    org_id: uuid.UUID,
    product_id: uuid.UUID,
    *,
    sale_price: Decimal | str | None = None,
    cost: Decimal | str | None = None,
    reorder_point: Decimal | str | None = None,
    is_active: bool | None = None,
    user_id: uuid.UUID | None = None,
) -> uuid.UUID:
    return await _run_setup(
        session,
        org_id,
        text(
            """
            SELECT ledger.update_product(
                CAST(:org AS uuid), CAST(:product AS uuid),
                CAST(:price AS numeric), CAST(:cost AS numeric),
                CAST(:reorder AS numeric), :active
            )
            """
        ),
        {
            "org": org_id,
            "product": product_id,
            "price": _quantize(sale_price) if sale_price is not None else None,
            "cost": _quantize(cost) if cost is not None else None,
            "reorder": _quantize(reorder_point) if reorder_point is not None else None,
            "active": is_active,
        },
        user_id=user_id,
    )


async def create_supplier(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    name: str,
    code: str | None = None,
    user_id: uuid.UUID | None = None,
) -> uuid.UUID:
    return await _run_setup(
        session,
        org_id,
        text(
            "SELECT ledger.create_supplier(CAST(:org AS uuid), :name, :code)"
        ),
        {"org": org_id, "name": name, "code": code},
        user_id=user_id,
    )


# ── Reference validation ─────────────────────────────────────


async def _require_customer(
    session: AsyncSession, org_id: uuid.UUID, customer_id: uuid.UUID
) -> None:
    found = await session.scalar(
        text(
            """
            SELECT 1
            FROM public.customers
            WHERE id = CAST(:id AS uuid)
              AND org_id = CAST(:org AS uuid)
              AND deleted_at IS NULL
              AND merged_into_id IS NULL
            """
        ),
        {"id": customer_id, "org": org_id},
    )
    if not found:
        raise PostingValidationError(
            "The customer does not exist in this organisation",
            code="customer_not_found",
            details={"customer_id": str(customer_id)},
        )


async def _require_supplier(
    session: AsyncSession, org_id: uuid.UUID, supplier_id: uuid.UUID
) -> None:
    supplier = await ledger_service.get_supplier(session, org_id, supplier_id)
    if supplier is None or not supplier.is_active:
        raise PostingValidationError(
            "The supplier does not exist in this organisation",
            code="supplier_not_found",
            details={"supplier_id": str(supplier_id)},
        )


async def _require_accounts(
    session: AsyncSession, org_id: uuid.UUID, codes: list[str]
) -> dict[str, ledger_service.Account]:
    accounts = await ledger_service.get_accounts_by_code(session, org_id, codes)
    missing = [
        code
        for code in codes
        if code not in accounts or not accounts[code].is_active
    ]
    if missing:
        raise PostingValidationError(
            "The organisation is missing required accounts",
            code="account_not_found",
            details={"account_codes": missing},
        )
    return accounts


async def _require_products(
    session: AsyncSession, org_id: uuid.UUID, products: list[Any]
) -> dict[uuid.UUID, ledger_service.Product]:
    ids = [line.product_id for line in products]
    if len(ids) != len(set(ids)):
        raise PostingValidationError(
            "A payload may contain each product only once",
            code="duplicate_product_line",
        )
    found = await ledger_service.get_products(session, org_id, ids)
    missing = [str(product_id) for product_id in ids if product_id not in found]
    inactive = [
        str(product_id)
        for product_id in ids
        if product_id in found and not found[product_id].is_active
    ]
    if missing or inactive:
        raise PostingValidationError(
            "The product does not exist or is inactive in this organisation",
            code="product_not_found",
            details={"missing": missing, "inactive": inactive},
        )
    return found


def _assert_declared_amount(
    computed: Decimal,
    declared: Decimal | None,
    *,
    label: str,
) -> None:
    if declared is None:
        return
    if _quantize(declared) != _quantize(computed):
        raise PostingValidationError(
            f"The {label} amount does not match the proposal amount",
            code="amount_mismatch",
            details={"computed": str(_quantize(computed)), "declared": str(declared)},
        )


def _assert_declared_currency(payload_currency: str, declared: str | None) -> None:
    if declared is None:
        return
    if payload_currency.upper() != declared.upper():
        raise PostingValidationError(
            "The proposal currency does not match the posting payload",
            code="currency_mismatch",
            details={"payload": payload_currency, "declared": declared},
        )


# ── Accounting periods ───────────────────────────────────────


async def create_accounting_period(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    start: date,
    end: date,
    user_id: uuid.UUID | None = None,
) -> uuid.UUID:
    if end < start:
        raise PostingValidationError(
            "The period end must not precede its start",
            code="invalid_period",
        )
    return await _run_setup(
        session,
        org_id,
        text(
            "SELECT ledger.create_accounting_period("
            "CAST(:org AS uuid), CAST(:start AS date), CAST(:end AS date))"
        ),
        {"org": org_id, "start": start, "end": end},
        user_id=user_id,
    )


async def set_accounting_period_state(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    start: date,
    end: date,
    closed: bool,
    user_id: uuid.UUID | None = None,
) -> uuid.UUID:
    if end < start:
        raise PostingValidationError(
            "The period end must not precede its start",
            code="invalid_period",
        )
    return await _run_setup(
        session,
        org_id,
        text(
            "SELECT ledger.set_accounting_period_state("
            "CAST(:org AS uuid), CAST(:start AS date), CAST(:end AS date), :closed)"
        ),
        {"org": org_id, "start": start, "end": end, "closed": closed},
        user_id=user_id,
    )


async def close_accounting_period(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    start: date,
    end: date,
    user_id: uuid.UUID | None = None,
) -> uuid.UUID:
    return await set_accounting_period_state(
        session,
        org_id,
        start=start,
        end=end,
        closed=True,
        user_id=user_id,
    )


async def reopen_accounting_period(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    start: date,
    end: date,
    user_id: uuid.UUID | None = None,
) -> uuid.UUID:
    return await set_accounting_period_state(
        session,
        org_id,
        start=start,
        end=end,
        closed=False,
        user_id=user_id,
    )


# ── Proposal dispatch ────────────────────────────────────────


async def post_proposal(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    proposal_id: uuid.UUID,
    proposal_type: str,
    payload: dict | None,
    amount: Decimal | str | None = None,
    currency: str | None = None,
    requested_by: uuid.UUID | None = None,
    role: str | None = None,
) -> PostingResult:
    """Post an approved proposal exactly once and return its journal entry."""
    kind = proposal_type.strip().lower()

    if kind == "manual":
        raise PostingValidationError(
            "Manual proposals are informational and are never posted",
            code="manual_not_postable",
        )

    if kind == "sale":
        return await post_sale(
            session,
            org_id=org_id,
            proposal_id=proposal_id,
            payload=payload,
            declared_amount=amount,
            declared_currency=currency,
            requested_by=requested_by,
            role=role,
        )
    if kind == "credit_note":
        return await post_credit_note(
            session,
            org_id=org_id,
            payload=payload,
            idempotency_key=proposal_id,
            source_type="proposal",
            source_id=proposal_id,
            declared_amount=amount,
            declared_currency=currency,
            requested_by=requested_by,
            role=role,
        )
    if kind == "expense":
        return await post_expense(
            session,
            org_id=org_id,
            proposal_id=proposal_id,
            payload=payload,
            declared_amount=amount,
            declared_currency=currency,
            requested_by=requested_by,
            role=role,
        )
    if kind == "opening_stock":
        return await post_opening_stock(
            session,
            org_id=org_id,
            payload=payload,
            idempotency_key=proposal_id,
            source_type="proposal",
            source_id=proposal_id,
            declared_amount=amount,
            requested_by=requested_by,
        )

    raise PostingValidationError(
        f"Proposal type '{proposal_type}' is not postable",
        code="unsupported_proposal_type",
        details={"proposal_type": proposal_type},
    )


# ── Sales ────────────────────────────────────────────────────


async def post_sale(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    proposal_id: uuid.UUID,
    payload: dict | None,
    declared_amount: Decimal | str | None = None,
    declared_currency: str | None = None,
    requested_by: uuid.UUID | None = None,
    role: str | None = None,
) -> PostingResult:
    sale = _parse(SalePayload, payload, "sale")
    _assert_declared_currency(sale.currency, declared_currency)

    async def operation() -> PostingResult:
        await ensure_default_chart(session, org_id)
        await _require_customer(session, org_id, sale.customer_id)
        products = await _require_products(session, org_id, sale.lines)

        revenue_total = _quantize(
            sum((line.quantity * line.unit_price for line in sale.lines), Decimal("0"))
        )
        if revenue_total <= 0:
            raise PostingValidationError(
                "A sale must have a positive value", code="invalid_sale_total"
            )
        _assert_declared_amount(revenue_total, declared_amount, label="sale")

        cogs_total = _quantize(
            sum((line.quantity * line.unit_cost for line in sale.lines), Decimal("0"))
        )

        required = [SALES_REVENUE, INVENTORY, COGS]
        if sale.payment.method == "cash":
            required.append(sale.payment.cash_account_code)
        else:
            required.append(RECEIVABLES)
        accounts = await _require_accounts(session, org_id, required)

        settlement_account = (
            accounts[sale.payment.cash_account_code]
            if sale.payment.method == "cash"
            else accounts[RECEIVABLES]
        )
        memo = sale.memo or "Sale"

        journal_lines = [
            _line(
                settlement_account.id,
                debit=revenue_total,
                description=f"Sale to customer {sale.customer_id}",
            ),
            _line(
                accounts[SALES_REVENUE].id,
                credit=revenue_total,
                description="Sales revenue",
            ),
        ]
        if cogs_total > 0:
            journal_lines.extend(
                [
                    _line(accounts[COGS].id, debit=cogs_total, description="Cost of goods sold"),
                    _line(accounts[INVENTORY].id, credit=cogs_total, description="Inventory relieved"),
                ]
            )

        entry_id = await session.scalar(
            text(
                """
                SELECT ledger.post_journal_entry(
                    CAST(:org AS uuid), 'proposal', CAST(:source AS uuid),
                    CAST(:key AS uuid), CAST(:effective AS timestamptz),
                    :memo, CAST(:lines AS jsonb), CAST(:requested_by AS uuid)
                )
                """
            ),
            {
                "org": org_id,
                "source": proposal_id,
                "key": proposal_id,
                "effective": sale.effective_at,
                "memo": memo,
                "lines": _json(journal_lines),
                "requested_by": requested_by,
            },
        )

        sale_lines = [
            {
                "product_id": str(line.product_id),
                "quantity": str(_quantize(line.quantity)),
                "unit_price": str(_quantize(line.unit_price)),
                "cogs_amount": str(_quantize(line.quantity * line.unit_cost)),
            }
            for line in sale.lines
        ]
        await session.execute(
            text(
                "SELECT ledger.post_sale_lines("
                "CAST(:org AS uuid), CAST(:entry AS uuid), CAST(:lines AS jsonb))"
            ),
            {"org": org_id, "entry": entry_id, "lines": _json(sale_lines)},
        )

        movement_ids: list[uuid.UUID] = []
        for line in sale.lines:
            product = products[line.product_id]
            movement_id = await session.scalar(
                text(
                    """
                    SELECT ledger.post_stock_movement(
                        CAST(:org AS uuid), CAST(:product AS uuid), CAST(:entry AS uuid),
                        CAST('sale' AS ledger.stock_movement_type),
                        CAST(:quantity AS numeric), CAST(:cost AS numeric),
                        'proposal', CAST(:source AS uuid),
                        CAST(:occurred AS timestamptz), :note
                    )
                    """
                ),
                {
                    "org": org_id,
                    "product": line.product_id,
                    "entry": entry_id,
                    "quantity": _quantize(-line.quantity),
                    "cost": _quantize(line.unit_cost),
                    "source": proposal_id,
                    "occurred": sale.effective_at,
                    "note": line.description or f"Sale of {product.sku}",
                },
            )
            movement_ids.append(movement_id)

        open_item_id = None
        if sale.payment.method == "receivable":
            open_item_id = await session.scalar(
                text(
                    """
                    SELECT ledger.create_open_item(
                        CAST(:org AS uuid),
                        CAST('customer_receivable' AS ledger.open_item_type),
                        CAST(:customer AS uuid), CAST(:entry AS uuid),
                        CAST(:amount AS numeric), CAST(:due_on AS date)
                    )
                    """
                ),
                {
                    "org": org_id,
                    "customer": sale.customer_id,
                    "entry": entry_id,
                    "amount": revenue_total,
                    "due_on": sale.payment.due_on,
                },
            )

        receipt_id = None
        issue_receipt = (
            sale.payment.issue_receipt
            if sale.payment.issue_receipt is not None
            else sale.payment.method == "cash"
        )
        if issue_receipt and sale.payment.method != "cash":
            raise PostingValidationError(
                "A receipt can only be issued for a paid sale",
                code="invalid_receipt_request",
            )
        if issue_receipt:
            receipt_lines = [
                {
                    "description": line.description
                    or f"{products[line.product_id].sku} — {products[line.product_id].name}",
                    "quantity": str(_quantize(line.quantity)),
                    "unit_price": str(_quantize(line.unit_price)),
                }
                for line in sale.lines
            ]
            receipt_id = await session.scalar(
                text(
                    """
                    SELECT ledger.issue_receipt(
                        CAST(:org AS uuid), CAST(:entry AS uuid), CAST(:currency AS char(3)),
                        CAST(:lines AS jsonb), CAST(:customer AS uuid), :notes
                    )
                    """
                ),
                {
                    "org": org_id,
                    "entry": entry_id,
                    "currency": sale.currency,
                    "lines": _json(receipt_lines),
                    "customer": sale.customer_id,
                    "notes": sale.payment.receipt_notes,
                },
            )

        return PostingResult(
            journal_entry_id=entry_id,
            receipt_id=receipt_id,
            open_item_id=open_item_id,
            stock_movement_ids=tuple(movement_ids),
        )

    return await _run(
        session,
        org_id=org_id,
        idempotency_key=proposal_id,
        operation=operation,
        user_id=requested_by,
        role=role,
    )


# ── Credit notes ─────────────────────────────────────────────


async def post_credit_note(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    payload: dict | None,
    idempotency_key: uuid.UUID,
    source_type: str = "credit_note",
    source_id: uuid.UUID | None = None,
    declared_amount: Decimal | str | None = None,
    declared_currency: str | None = None,
    requested_by: uuid.UUID | None = None,
    role: str | None = None,
) -> PostingResult:
    credit = _parse(CreditNotePayload, payload, "credit note")
    source_id = source_id or idempotency_key

    async def operation() -> PostingResult:
        await ensure_default_chart(session, org_id)

        receipt = None
        if credit.original_receipt_id is not None:
            receipt = (
                await session.execute(
                    text(
                        """
                        SELECT id,
                               receipt_type::text AS receipt_type,
                               currency_code::text AS currency_code,
                               customer_id,
                               total_amount
                        FROM ledger.receipts
                        WHERE org_id = CAST(:org AS uuid)
                          AND id = CAST(:receipt AS uuid)
                        """
                    ),
                    {"org": org_id, "receipt": credit.original_receipt_id},
                )
            ).one_or_none()
            if receipt is None:
                raise PostingValidationError(
                    "The original receipt does not exist in this organisation",
                    code="receipt_not_found",
                    details={"receipt_id": str(credit.original_receipt_id)},
                )
            if receipt.receipt_type != "receipt":
                raise PostingValidationError(
                    "A credit note must reference a receipt, not another credit note",
                    code="invalid_credit_note_source",
                )

        if receipt is not None:
            if (
                credit.customer_id is not None
                and credit.customer_id != receipt.customer_id
            ):
                raise PostingValidationError(
                    "The credit note customer does not match the receipt",
                    code="customer_mismatch",
                )
            customer_id = credit.customer_id or receipt.customer_id
            if (
                credit.currency is not None
                and credit.currency != receipt.currency_code
            ):
                raise PostingValidationError(
                    "The credit note currency does not match the receipt",
                    code="currency_mismatch",
                )
            currency = credit.currency or receipt.currency_code
        else:
            customer_id = credit.customer_id
            currency = credit.currency

        if customer_id is None:
            raise PostingValidationError(
                "A credit note requires a customer", code="invalid_credit_note"
            )
        if currency is None:
            raise PostingValidationError(
                "A credit note requires a currency", code="invalid_credit_note"
            )

        _assert_declared_currency(currency, declared_currency)
        await _require_customer(session, org_id, customer_id)

        revenue_total = _quantize(
            sum(
                (line.quantity * line.unit_price for line in credit.lines),
                Decimal("0"),
            )
        )
        if revenue_total <= 0:
            raise PostingValidationError(
                "A credit note must have a positive value",
                code="invalid_credit_note_total",
            )
        _assert_declared_amount(revenue_total, declared_amount, label="credit note")

        if receipt is not None:
            prior_credits = _quantize(
                await session.scalar(
                    text(
                        """
                        SELECT coalesce(sum(total_amount), 0)
                        FROM ledger.receipts
                        WHERE org_id = CAST(:org AS uuid)
                          AND credit_note_of_id = CAST(:receipt AS uuid)
                          AND receipt_type = 'credit_note'
                        """
                    ),
                    {"org": org_id, "receipt": receipt.id},
                )
                or Decimal("0")
            )
            receipt_total = _quantize(receipt.total_amount)
            if prior_credits + revenue_total > receipt_total:
                raise PostingValidationError(
                    "The credit note exceeds the uncredited receipt value",
                    code="invalid_credit_note_amount",
                    details={
                        "receipt_total": str(receipt_total),
                        "already_credited": str(prior_credits),
                        "requested": str(revenue_total),
                    },
                )

        if credit.restock and any(
            line.product_id is None or line.unit_cost is None for line in credit.lines
        ):
            raise PostingValidationError(
                "Restocked credit-note lines require a product and a unit cost",
                code="invalid_credit_note_line",
            )
        products = (
            await _require_products(session, org_id, credit.lines)
            if credit.restock
            else {}
        )
        cost_total = _quantize(
            sum(
                (line.quantity * (line.unit_cost or Decimal("0")) for line in credit.lines),
                Decimal("0"),
            )
        )

        required = [SALES_REVENUE, credit.cash_account_code]
        if credit.restock:
            required.extend([INVENTORY, COGS])
        accounts = await _require_accounts(session, org_id, required)

        journal_lines = [
            _line(
                accounts[SALES_REVENUE].id,
                debit=revenue_total,
                description="Sales return",
            ),
            _line(
                accounts[credit.cash_account_code].id,
                credit=revenue_total,
                description="Cash refund",
            ),
        ]
        if credit.restock and cost_total > 0:
            journal_lines.extend(
                [
                    _line(
                        accounts[INVENTORY].id,
                        debit=cost_total,
                        description="Inventory returned",
                    ),
                    _line(
                        accounts[COGS].id,
                        credit=cost_total,
                        description="Cost of goods returned",
                    ),
                ]
            )

        entry_id = await session.scalar(
            text(
                """
                SELECT ledger.post_journal_entry(
                    CAST(:org AS uuid), :source_type, CAST(:source AS uuid),
                    CAST(:key AS uuid), CAST(:effective AS timestamptz),
                    :memo, CAST(:lines AS jsonb), CAST(:requested_by AS uuid)
                )
                """
            ),
            {
                "org": org_id,
                "source_type": source_type,
                "source": source_id,
                "key": idempotency_key,
                "effective": credit.effective_at,
                "memo": credit.memo or "Credit note",
                "lines": _json(journal_lines),
                "requested_by": requested_by,
            },
        )

        movement_ids: list[uuid.UUID] = []
        if credit.restock:
            for line in credit.lines:
                product = products[line.product_id]
                movement_id = await session.scalar(
                    text(
                        """
                        SELECT ledger.post_stock_movement(
                            CAST(:org AS uuid), CAST(:product AS uuid), CAST(:entry AS uuid),
                            CAST('return_in' AS ledger.stock_movement_type),
                            CAST(:quantity AS numeric), CAST(:cost AS numeric),
                            :source_type, CAST(:source AS uuid),
                            CAST(:occurred AS timestamptz), :note
                        )
                        """
                    ),
                    {
                        "org": org_id,
                        "product": line.product_id,
                        "entry": entry_id,
                        "quantity": _quantize(line.quantity),
                        "cost": _quantize(line.unit_cost),
                        "source_type": source_type,
                        "source": source_id,
                        "occurred": credit.effective_at,
                        "note": line.description or f"Return of {product.sku}",
                    },
                )
                movement_ids.append(movement_id)

        document_lines = []
        for index, line in enumerate(credit.lines, start=1):
            product = products.get(line.product_id) if line.product_id else None
            description = line.description or (
                f"{product.sku} — {product.name}"
                if product is not None
                else f"Credit note item {index}"
            )
            document_lines.append(
                {
                    "description": description,
                    "quantity": str(_quantize(line.quantity)),
                    "unit_price": str(_quantize(line.unit_price)),
                }
            )

        credit_note_id = await session.scalar(
            text(
                """
                SELECT ledger.issue_credit_note(
                    CAST(:org AS uuid), CAST(:entry AS uuid), CAST(:currency AS char(3)),
                    CAST(:lines AS jsonb), CAST(:customer AS uuid), :notes,
                    CAST(:original AS uuid)
                )
                """
            ),
            {
                "org": org_id,
                "entry": entry_id,
                "currency": currency,
                "lines": _json(document_lines),
                "customer": customer_id,
                "notes": credit.notes,
                "original": credit.original_receipt_id,
            },
        )

        return PostingResult(
            journal_entry_id=entry_id,
            credit_note_id=credit_note_id,
            stock_movement_ids=tuple(movement_ids),
        )

    return await _run(
        session,
        org_id=org_id,
        idempotency_key=idempotency_key,
        operation=operation,
        user_id=requested_by,
        role=role,
    )


# ── Expenses ─────────────────────────────────────────────────


async def post_expense(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    proposal_id: uuid.UUID,
    payload: dict | None,
    declared_amount: Decimal | str | None = None,
    declared_currency: str | None = None,
    requested_by: uuid.UUID | None = None,
    role: str | None = None,
) -> PostingResult:
    expense = _parse(ExpensePayload, payload, "expense")
    _assert_declared_currency(expense.currency, declared_currency)
    _assert_declared_amount(expense.amount, declared_amount, label="expense")

    async def operation() -> PostingResult:
        await ensure_default_chart(session, org_id)

        account_code = expense.expense_account_code
        if not account_code:
            if not expense.category:
                raise PostingValidationError(
                    "An expense needs an expense account code or a category",
                    code="missing_expense_account",
                )
            account_code = EXPENSE_CATEGORY_ACCOUNTS.get(
                expense.category.strip().lower()
            )
            if not account_code:
                raise PostingValidationError(
                    "Unknown expense category",
                    code="unknown_expense_category",
                    details={"category": expense.category},
                )

        required = [account_code]
        if expense.payment.method == "cash":
            required.append(expense.payment.cash_account_code)
        else:
            required.append(PAYABLES)
        accounts = await _require_accounts(session, org_id, required)
        if accounts[account_code].account_type not in {"expense", "cogs"}:
            raise PostingValidationError(
                "The selected account is not an expense account",
                code="invalid_expense_account",
            )

        supplier_id = expense.payment.supplier_id
        if expense.payment.method == "payable":
            if supplier_id is None:
                raise PostingValidationError(
                    "A payable expense requires supplier_id",
                    code="missing_supplier",
                )
            await _require_supplier(session, org_id, supplier_id)
            credit_account = accounts[PAYABLES]
        else:
            credit_account = accounts[expense.payment.cash_account_code]

        memo = expense.description or expense.vendor or "Expense"
        entry_id = await session.scalar(
            text(
                """
                SELECT ledger.post_journal_entry(
                    CAST(:org AS uuid), 'proposal', CAST(:source AS uuid),
                    CAST(:key AS uuid), CAST(:effective AS timestamptz),
                    :memo, CAST(:lines AS jsonb), CAST(:requested_by AS uuid)
                )
                """
            ),
            {
                "org": org_id,
                "source": proposal_id,
                "key": proposal_id,
                "effective": expense.effective_at,
                "memo": memo,
                "lines": _json(
                    [
                        _line(
                            accounts[account_code].id,
                            debit=expense.amount,
                            description=expense.vendor,
                        ),
                        _line(
                            credit_account.id,
                            credit=expense.amount,
                            description=expense.vendor,
                        ),
                    ]
                ),
                "requested_by": requested_by,
            },
        )

        open_item_id = None
        if expense.payment.method == "payable":
            open_item_id = await session.scalar(
                text(
                    """
                    SELECT ledger.create_open_item(
                        CAST(:org AS uuid),
                        CAST('supplier_payable' AS ledger.open_item_type),
                        CAST(:supplier AS uuid), CAST(:entry AS uuid),
                        CAST(:amount AS numeric), CAST(:due_on AS date)
                    )
                    """
                ),
                {
                    "org": org_id,
                    "supplier": supplier_id,
                    "entry": entry_id,
                    "amount": expense.amount,
                    "due_on": expense.payment.due_on,
                },
            )

        return PostingResult(
            journal_entry_id=entry_id,
            open_item_id=open_item_id,
        )

    return await _run(
        session,
        org_id=org_id,
        idempotency_key=proposal_id,
        operation=operation,
        user_id=requested_by,
        role=role,
    )


# ── Settlements ──────────────────────────────────────────────


async def settle_open_item(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    open_item_id: uuid.UUID,
    amount: Decimal | str,
    effective_at: datetime,
    idempotency_key: uuid.UUID,
    cash_account_code: str = CASH,
    currency: str | None = None,
    issue_receipt: bool = False,
    notes: str | None = None,
    source_id: uuid.UUID | None = None,
    requested_by: uuid.UUID | None = None,
    role: str | None = None,
) -> PostingResult:
    """Settle a receivable or payable, optionally issuing a receipt."""
    settlement_amount = _quantize(amount)
    if settlement_amount <= 0:
        raise PostingValidationError(
            "A settlement must have a positive amount",
            code="invalid_settlement_amount",
        )

    effective_at = _aware_utc(effective_at)
    safe_currency = _upper_currency(currency) if currency else None
    if safe_currency is not None and (
        len(safe_currency) != 3 or not safe_currency.isalpha()
    ):
        raise PostingValidationError(
            "A three letter currency code is required",
            code="invalid_currency",
        )
    if issue_receipt and safe_currency is None:
        raise PostingValidationError(
            "A receipt requires a currency",
            code="invalid_receipt_request",
        )

    source_id = source_id or idempotency_key

    async def operation() -> PostingResult:
        item = await ledger_service.get_open_item(session, org_id, open_item_id)
        if item is None:
            raise PostingValidationError(
                "The open item does not exist in this organisation",
                code="open_item_not_found",
            )
        outstanding = _quantize(item.outstanding_amount)
        if outstanding <= 0:
            raise PostingConflictError(
                "The open item is already settled", code="open_item_settled"
            )
        if settlement_amount > outstanding:
            raise PostingValidationError(
                "The settlement exceeds the outstanding balance",
                code="invalid_settlement_amount",
                details={
                    "outstanding": str(outstanding),
                    "requested": str(settlement_amount),
                },
            )
        if issue_receipt and item.item_type != "customer_receivable":
            raise PostingValidationError(
                "Only a customer receivable can issue a receipt",
                code="invalid_receipt_request",
            )

        if item.item_type == "customer_receivable":
            required = [RECEIVABLES, cash_account_code]
        elif item.item_type == "supplier_payable":
            required = [PAYABLES, cash_account_code]
        else:
            raise PostingValidationError(
                "Unsupported open-item type",
                code="invalid_open_item_type",
                details={"item_type": item.item_type},
            )
        accounts = await _require_accounts(session, org_id, required)
        cash_account = accounts[cash_account_code]

        if item.item_type == "customer_receivable":
            journal_lines = [
                _line(
                    cash_account.id,
                    debit=settlement_amount,
                    description="Payment received",
                ),
                _line(
                    accounts[RECEIVABLES].id,
                    credit=settlement_amount,
                    description="Receivable settled",
                ),
            ]
        else:
            journal_lines = [
                _line(
                    accounts[PAYABLES].id,
                    debit=settlement_amount,
                    description="Payable settled",
                ),
                _line(
                    cash_account.id,
                    credit=settlement_amount,
                    description="Payment made",
                ),
            ]

        entry_id = await session.scalar(
            text(
                """
                SELECT ledger.post_journal_entry(
                    CAST(:org AS uuid), 'settlement', CAST(:source AS uuid),
                    CAST(:key AS uuid), CAST(:effective AS timestamptz),
                    :memo, CAST(:lines AS jsonb), CAST(:requested_by AS uuid)
                )
                """
            ),
            {
                "org": org_id,
                "source": source_id,
                "key": idempotency_key,
                "effective": effective_at,
                "memo": notes or f"Settlement of {item.item_type}",
                "lines": _json(journal_lines),
                "requested_by": requested_by,
            },
        )

        allocation_id = await session.scalar(
            text(
                """
                SELECT ledger.allocate_payment(
                    CAST(:org AS uuid), CAST(:item AS uuid),
                    CAST(:entry AS uuid), CAST(:amount AS numeric), NULL
                )
                """
            ),
            {
                "org": org_id,
                "item": open_item_id,
                "entry": entry_id,
                "amount": settlement_amount,
            },
        )

        receipt_id = None
        if issue_receipt:
            receipt_id = await session.scalar(
                text(
                    """
                    SELECT ledger.issue_receipt(
                        CAST(:org AS uuid), CAST(:entry AS uuid),
                        CAST(:currency AS char(3)), CAST(:lines AS jsonb),
                        CAST(:customer AS uuid), :notes
                    )
                    """
                ),
                {
                    "org": org_id,
                    "entry": entry_id,
                    "currency": safe_currency,
                    "lines": _json(
                        [
                            {
                                "description": notes or "Payment received",
                                "quantity": "1",
                                "unit_price": str(settlement_amount),
                            }
                        ]
                    ),
                    "customer": item.counterparty_id,
                    "notes": notes,
                },
            )

        return PostingResult(
            journal_entry_id=entry_id,
            receipt_id=receipt_id,
            open_item_id=open_item_id,
            allocation_id=allocation_id,
        )

    return await _run(
        session,
        org_id=org_id,
        idempotency_key=idempotency_key,
        operation=operation,
        user_id=requested_by,
        role=role,
    )


async def reverse_settlement(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    allocation_id: uuid.UUID,
    effective_at: datetime | None = None,
    idempotency_key: uuid.UUID | None = None,
    source_id: uuid.UUID | None = None,
    requested_by: uuid.UUID | None = None,
    role: str | None = None,
) -> PostingResult:
    """Reverse a settlement and reopen its open item; safe to retry."""
    effective_at = effective_at or datetime.now(timezone.utc)
    effective_at = _aware_utc(effective_at)
    key = idempotency_key or allocation_id
    source_id = source_id or allocation_id

    async def operation() -> PostingResult:
        row = (
            await session.execute(
                text(
                    """
                    SELECT pa.open_item_id, pa.payment_journal_entry_id, pa.amount,
                           EXISTS (
                               SELECT 1
                               FROM ledger.payment_allocations rev
                               WHERE rev.org_id = pa.org_id
                                 AND rev.reversal_of_id = pa.id
                           ) AS has_reversal
                    FROM ledger.payment_allocations pa
                    WHERE pa.org_id = CAST(:org AS uuid)
                      AND pa.id = CAST(:id AS uuid)
                    """
                ),
                {"org": org_id, "id": allocation_id},
            )
        ).one_or_none()
        if row is None:
            raise PostingValidationError(
                "The settlement does not exist in this organisation",
                code="settlement_not_found",
            )

        amount = Decimal(row.amount)
        if amount <= 0:
            raise PostingValidationError(
                "Only a positive settlement can be reversed",
                code="invalid_settlement_reversal",
            )
        if row.has_reversal:
            raise PostingConflictError(
                "The settlement has already been reversed",
                code="settlement_already_reversed",
            )

        reversal_entry_id = await session.scalar(
            text(
                """
                SELECT ledger.reverse_journal_entry(
                    CAST(:org AS uuid), CAST(:original AS uuid),
                    'settlement_reversal', CAST(:source AS uuid),
                    CAST(:key AS uuid), CAST(:effective AS timestamptz),
                    :memo, CAST(:requested_by AS uuid)
                )
                """
            ),
            {
                "org": org_id,
                "original": row.payment_journal_entry_id,
                "source": source_id,
                "key": key,
                "effective": effective_at,
                "memo": f"Reversal of settlement {allocation_id}",
                "requested_by": requested_by,
            },
        )

        reversal_allocation_id = await session.scalar(
            text(
                """
                SELECT ledger.allocate_payment(
                    CAST(:org AS uuid), CAST(:item AS uuid),
                    CAST(:entry AS uuid), CAST(:amount AS numeric),
                    CAST(:reversal_of AS uuid)
                )
                """
            ),
            {
                "org": org_id,
                "item": row.open_item_id,
                "entry": reversal_entry_id,
                "amount": -amount,
                "reversal_of": allocation_id,
            },
        )

        return PostingResult(
            journal_entry_id=reversal_entry_id,
            open_item_id=row.open_item_id,
            allocation_id=reversal_allocation_id,
        )

    return await _run(
        session,
        org_id=org_id,
        idempotency_key=key,
        operation=operation,
        user_id=requested_by,
        role=role,
    )


# ── Opening stock ────────────────────────────────────────────


async def post_opening_stock(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    payload: dict | None,
    idempotency_key: uuid.UUID,
    source_type: str = "opening_stock",
    source_id: uuid.UUID | None = None,
    declared_amount: Decimal | str | None = None,
    requested_by: uuid.UUID | None = None,
    role: str | None = None,
) -> PostingResult:
    opening = _parse(OpeningStockPayload, payload, "opening stock")
    source_id = source_id or idempotency_key

    async def operation() -> PostingResult:
        await ensure_default_chart(session, org_id)
        await _require_products(session, org_id, opening.lines)
        total_cost = _quantize(
            sum(
                (line.quantity * line.unit_cost for line in opening.lines),
                Decimal("0"),
            )
        )
        if total_cost <= 0:
            raise PostingValidationError(
                "Opening stock must have a positive cost value",
                code="invalid_opening_stock",
            )
        _assert_declared_amount(total_cost, declared_amount, label="opening stock")

        accounts = await _require_accounts(session, org_id, [INVENTORY, OPENING_EQUITY])
        entry_id = await session.scalar(
            text(
                """
                SELECT ledger.post_journal_entry(
                    CAST(:org AS uuid), :source_type, CAST(:source AS uuid),
                    CAST(:key AS uuid), CAST(:effective AS timestamptz),
                    :memo, CAST(:lines AS jsonb), CAST(:requested_by AS uuid)
                )
                """
            ),
            {
                "org": org_id,
                "source_type": source_type,
                "source": source_id,
                "key": idempotency_key,
                "effective": opening.effective_at,
                "memo": opening.memo or "Opening stock",
                "lines": _json(
                    [
                        _line(
                            accounts[INVENTORY].id,
                            debit=total_cost,
                            description="Opening inventory",
                        ),
                        _line(
                            accounts[OPENING_EQUITY].id,
                            credit=total_cost,
                            description="Opening equity",
                        ),
                    ]
                ),
                "requested_by": requested_by,
            },
        )

        movement_ids: list[uuid.UUID] = []
        for line in opening.lines:
            movement_id = await session.scalar(
                text(
                    """
                    SELECT ledger.post_stock_movement(
                        CAST(:org AS uuid), CAST(:product AS uuid), CAST(:entry AS uuid),
                        CAST('opening_balance' AS ledger.stock_movement_type),
                        CAST(:quantity AS numeric), CAST(:cost AS numeric),
                        :source_type, CAST(:source AS uuid),
                        CAST(:occurred AS timestamptz), :note
                    )
                    """
                ),
                {
                    "org": org_id,
                    "product": line.product_id,
                    "entry": entry_id,
                    "quantity": _quantize(line.quantity),
                    "cost": _quantize(line.unit_cost),
                    "source_type": source_type,
                    "source": source_id,
                    "occurred": opening.effective_at,
                    "note": line.description or "Opening stock",
                },
            )
            movement_ids.append(movement_id)

        return PostingResult(
            journal_entry_id=entry_id,
            stock_movement_ids=tuple(movement_ids),
        )

    return await _run(
        session,
        org_id=org_id,
        idempotency_key=idempotency_key,
        operation=operation,
        user_id=requested_by,
        role=role,
    )


# ── Reversal ─────────────────────────────────────────────────


async def reverse_posting(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    journal_entry_id: uuid.UUID,
    effective_at: datetime | None = None,
    idempotency_key: uuid.UUID | None = None,
    source_id: uuid.UUID | None = None,
    requested_by: uuid.UUID | None = None,
    role: str | None = None,
) -> PostingResult:
    """Reverse a posted entry and its stock movements; safe to retry.

    Receipts and open items are historical documents; this MVP reversal reverses
    the journal and inventory effect only.  A credit note or settlement
    reversal is a separate, later posting.
    """
    effective_at = effective_at or datetime.now(timezone.utc)
    key = idempotency_key or journal_entry_id
    source_id = source_id or journal_entry_id

    async def operation() -> PostingResult:
        original = await ledger_service.get_journal_entry(
            session, org_id, journal_entry_id
        )
        if original is None:
            raise PostingValidationError(
                "The journal entry does not exist in this organisation",
                code="journal_entry_not_found",
            )

        reversal_id = await session.scalar(
            text(
                """
                SELECT ledger.reverse_journal_entry(
                    CAST(:org AS uuid), CAST(:original AS uuid),
                    'reversal', CAST(:source AS uuid), CAST(:key AS uuid),
                    CAST(:effective AS timestamptz), :memo, CAST(:requested_by AS uuid)
                )
                """
            ),
            {
                "org": org_id,
                "original": journal_entry_id,
                "source": source_id,
                "key": key,
                "effective": effective_at,
                "memo": f"Reversal of entry {original.entry_number}",
                "requested_by": requested_by,
            },
        )

        movements = (
            await session.execute(
                text(
                    """
                    SELECT product_id, movement_type, quantity_delta, unit_cost
                    FROM ledger.stock_movements
                    WHERE org_id = CAST(:org AS uuid)
                      AND journal_entry_id = CAST(:entry AS uuid)
                    """
                ),
                {"org": org_id, "entry": journal_entry_id},
            )
        ).all()

        movement_ids: list[uuid.UUID] = []
        for movement in movements:
            if movement.movement_type == "sale":
                movement_type = "sale_reversal"
            elif movement.quantity_delta > 0:
                movement_type = "adjustment_out"
            else:
                movement_type = "return_in"
            movement_id = await session.scalar(
                text(
                    """
                    SELECT ledger.post_stock_movement(
                        CAST(:org AS uuid), CAST(:product AS uuid), CAST(:entry AS uuid),
                        CAST(:kind AS ledger.stock_movement_type),
                        CAST(:quantity AS numeric), CAST(:cost AS numeric),
                        'journal_reversal', CAST(:source AS uuid),
                        CAST(:occurred AS timestamptz), :note
                    )
                    """
                ),
                {
                    "org": org_id,
                    "product": movement.product_id,
                    "entry": reversal_id,
                    "kind": movement_type,
                    "quantity": _quantize(-movement.quantity_delta),
                    "cost": _quantize(movement.unit_cost),
                    "source": reversal_id,
                    "occurred": effective_at,
                    "note": f"Reversal of entry {original.entry_number}",
                },
            )
            movement_ids.append(movement_id)

        return PostingResult(
            journal_entry_id=reversal_id,
            stock_movement_ids=tuple(movement_ids),
        )

    return await _run(
        session,
        org_id=org_id,
        idempotency_key=key,
        operation=operation,
        user_id=requested_by,
        role=role,
    )
