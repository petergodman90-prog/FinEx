"""Sales and credit-note staging on top of proposals."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TokenPayload
from app.models.customer import Customer
from app.models.proposal import Proposal
from app.schemas.proposal import ProposalCreate
from app.schemas.sale import (
    CreditNoteCreate,
    CreditNoteCreateOut,
    CreditNoteLineOut,
    CreditNoteOut,
    SaleCreate,
    SaleCreateOut,
    SaleLineOut,
    SaleOut,
    SalePaymentOut,
)
from app.services import customer_service, ledger_service, proposal_service
from app.services.errors import DomainValidationError, NotFoundError

ZERO = Decimal("0")
FOUR_PLACES = Decimal("0.0001")
TWO_PLACES = Decimal("0.01")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    return _as_utc(value).isoformat()


def _uuid_or_none(value: object) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def _date_or_none(value: object) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def _datetime_or_none(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def _decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return ZERO


def _proposal_amount(total: Decimal) -> Decimal:
    total = total.quantize(FOUR_PLACES)
    if total <= ZERO:
        raise DomainValidationError(
            "The proposal total must be positive",
            details={"total": str(total)},
        )
    amount = total.quantize(TWO_PLACES)
    if amount != total:
        raise DomainValidationError(
            "The proposal total must have at most two decimal places",
            details={"total": str(total)},
        )
    return amount


def _require_sale_customer(
    customer: Customer | None, customer_id: uuid.UUID
) -> Customer:
    if customer is None:
        raise DomainValidationError(
            "The customer does not exist in this organisation",
            details={"customer_id": str(customer_id)},
        )
    if customer.merged_into_id is not None:
        raise DomainValidationError(
            "The customer has been merged into another customer",
            details={
                "customer_id": str(customer_id),
                "merged_into_id": str(customer.merged_into_id),
            },
        )
    return customer


async def _require_sale_products(
    session: AsyncSession,
    org_id: uuid.UUID,
    product_ids: list[uuid.UUID],
) -> dict[uuid.UUID, ledger_service.Product]:
    products = await ledger_service.get_products(session, org_id, product_ids)
    missing = [str(pid) for pid in product_ids if pid not in products]
    inactive = [
        str(pid)
        for pid in product_ids
        if pid in products and not products[pid].is_active
    ]
    if missing or inactive:
        raise DomainValidationError(
            "One or more products do not exist or are inactive",
            details={"missing": missing, "inactive": inactive},
        )
    return products


async def _original_sale_lines(
    session: AsyncSession, org_id: uuid.UUID, receipt: ledger_service.Receipt
) -> dict[uuid.UUID, tuple[Decimal, Decimal]] | None:
    """Return product quantity/cost snapshots for the sale behind a receipt."""
    proposal = await session.scalar(
        select(Proposal)
        .where(
            Proposal.org_id == org_id,
            Proposal.journal_entry_id == receipt.journal_entry_id,
            Proposal.proposal_type == "sale",
            Proposal.deleted_at.is_(None),
        )
        .order_by(Proposal.created_at.desc())
        .limit(1)
    )
    if proposal is None:
        return None
    lines: dict[uuid.UUID, tuple[Decimal, Decimal]] = {}
    for line in (proposal.payload or {}).get("lines") or []:
        product_id = _uuid_or_none(line.get("product_id"))
        if product_id is None:
            continue
        lines[product_id] = (
            _decimal(line.get("quantity")),
            _decimal(line.get("unit_cost")),
        )
    return lines


async def _prior_credit_quantities(
    session: AsyncSession, org_id: uuid.UUID, receipt_id: uuid.UUID
) -> dict[uuid.UUID, Decimal]:
    """Quantities already credited against a receipt, by product."""
    rows = await session.execute(
        text(
            """
            SELECT payload
            FROM proposals
            WHERE org_id = CAST(:org AS uuid)
              AND proposal_type = 'credit_note'
              AND status = 'applied'
              AND deleted_at IS NULL
              AND payload ->> 'original_receipt_id' = :receipt
            """
        ),
        {"org": org_id, "receipt": str(receipt_id)},
    )
    quantities: dict[uuid.UUID, Decimal] = {}
    for row in rows:
        for line in (row.payload or {}).get("lines") or []:
            product_id = _uuid_or_none(line.get("product_id"))
            if product_id is None:
                continue
            quantities[product_id] = (
                quantities.get(product_id, ZERO) + _decimal(line.get("quantity"))
            )
    return quantities


def _validate_credit_lines_against_sale(
    original_lines: dict[uuid.UUID, tuple[Decimal, Decimal]],
    prior_quantities: dict[uuid.UUID, Decimal],
    lines: list,
) -> None:
    for line in lines:
        if line.product_id is None:
            continue
        if line.product_id not in original_lines:
            raise DomainValidationError(
                "A credit note line must belong to the original sale",
                details={"product_id": str(line.product_id)},
            )
        sold_quantity, _ = original_lines[line.product_id]
        remaining = sold_quantity - prior_quantities.get(line.product_id, ZERO)
        if line.quantity > remaining:
            raise DomainValidationError(
                "A credit note quantity exceeds the unreturned sale quantity",
                details={
                    "product_id": str(line.product_id),
                    "sold": str(sold_quantity),
                    "already_credited": str(
                        prior_quantities.get(line.product_id, ZERO)
                    ),
                    "requested": str(line.quantity),
                },
            )


async def _customer_names(
    session: AsyncSession, org_id: uuid.UUID, customer_ids: set[uuid.UUID]
) -> dict[uuid.UUID, str]:
    if not customer_ids:
        return {}
    rows = (
        await session.execute(
            select(Customer.id, Customer.name).where(
                Customer.org_id == org_id,
                Customer.id.in_(customer_ids),
            )
        )
    ).all()
    return {row.id: row.name for row in rows}


def _sale_line(line: dict, products: dict) -> SaleLineOut | None:
    product_id = _uuid_or_none(line.get("product_id"))
    if product_id is None:
        return None
    product = products.get(product_id)
    quantity = _decimal(line.get("quantity"))
    unit_price = _decimal(line.get("unit_price"))
    unit_cost = line.get("unit_cost")
    return SaleLineOut(
        product_id=product_id,
        sku=product.sku if product is not None else None,
        name=product.name if product is not None else None,
        quantity=quantity,
        unit_price=unit_price,
        unit_cost=_decimal(unit_cost) if unit_cost is not None else None,
        line_total=(quantity * unit_price).quantize(FOUR_PLACES),
        description=line.get("description"),
    )


def _credit_note_line(line: dict, products: dict) -> CreditNoteLineOut:
    product_id = _uuid_or_none(line.get("product_id"))
    product = products.get(product_id) if product_id is not None else None
    quantity = _decimal(line.get("quantity"))
    unit_price = _decimal(line.get("unit_price"))
    unit_cost = line.get("unit_cost")
    return CreditNoteLineOut(
        product_id=product_id,
        sku=product.sku if product is not None else None,
        name=product.name if product is not None else None,
        quantity=quantity,
        unit_price=unit_price,
        unit_cost=_decimal(unit_cost) if unit_cost is not None else None,
        line_total=(quantity * unit_price).quantize(FOUR_PLACES),
        description=line.get("description"),
    )


def _payment(payload: dict) -> SalePaymentOut:
    payment = dict(payload.get("payment") or {})
    method = str(payment.get("method", "cash"))
    issue_receipt = payment.get("issue_receipt")
    return SalePaymentOut(
        method=method,
        cash_account_code=str(payment.get("cash_account_code", "1000")),
        due_on=_date_or_none(payment.get("due_on")),
        issue_receipt=bool(
            issue_receipt if issue_receipt is not None else method == "cash"
        ),
        receipt_notes=payment.get("receipt_notes"),
    )


async def _serialize_sales(
    session: AsyncSession, org_id: uuid.UUID, proposals: list[Proposal]
) -> list[SaleOut]:
    if not proposals:
        return []
    customer_ids: set[uuid.UUID] = set()
    product_ids: set[uuid.UUID] = set()
    journal_entry_ids: list[uuid.UUID] = []
    for proposal in proposals:
        payload = dict(proposal.payload or {})
        customer_id = _uuid_or_none(payload.get("customer_id"))
        if customer_id is not None:
            customer_ids.add(customer_id)
        for line in payload.get("lines") or []:
            product_id = _uuid_or_none(line.get("product_id"))
            if product_id is not None:
                product_ids.add(product_id)
        if proposal.journal_entry_id is not None:
            journal_entry_ids.append(proposal.journal_entry_id)

    customers = await _customer_names(session, org_id, customer_ids)
    products = await ledger_service.get_products(
        session, org_id, list(product_ids)
    )
    documents = await ledger_service.get_posting_documents(
        session, org_id, journal_entry_ids
    )

    results: list[SaleOut] = []
    for proposal in proposals:
        payload = dict(proposal.payload or {})
        customer_id = _uuid_or_none(payload.get("customer_id"))
        document = documents.get(proposal.journal_entry_id)
        receipt_id = (
            document.receipt_id
            if document is not None and document.receipt_type == "receipt"
            else None
        )
        results.append(
            SaleOut(
                id=proposal.id,
                proposal_type=proposal.proposal_type,
                title=proposal.title,
                status=proposal.status,
                amount=proposal.amount,
                currency=proposal.currency,
                effective_at=_datetime_or_none(payload.get("effective_at")),
                customer_id=customer_id,
                customer_name=customers.get(customer_id) if customer_id else None,
                lines=[
                    line
                    for line in (
                        _sale_line(item, products)
                        for item in (payload.get("lines") or [])
                    )
                    if line is not None
                ],
                payment=_payment(payload),
                memo=payload.get("memo"),
                journal_entry_id=proposal.journal_entry_id,
                receipt_id=receipt_id,
                receipt_number=document.receipt_number
                if receipt_id is not None and document is not None
                else None,
                open_item_id=document.open_item_id if document is not None else None,
                posted_at=proposal.posted_at,
                posting_error=dict(proposal.posting_error)
                if proposal.posting_error is not None
                else None,
                created_at=proposal.created_at,
                updated_at=proposal.updated_at,
            )
        )
    return results


async def _serialize_credit_notes(
    session: AsyncSession, org_id: uuid.UUID, proposals: list[Proposal]
) -> list[CreditNoteOut]:
    if not proposals:
        return []
    customer_ids: set[uuid.UUID] = set()
    product_ids: set[uuid.UUID] = set()
    journal_entry_ids: list[uuid.UUID] = []
    for proposal in proposals:
        payload = dict(proposal.payload or {})
        customer_id = _uuid_or_none(payload.get("customer_id"))
        if customer_id is not None:
            customer_ids.add(customer_id)
        for line in payload.get("lines") or []:
            product_id = _uuid_or_none(line.get("product_id"))
            if product_id is not None:
                product_ids.add(product_id)
        if proposal.journal_entry_id is not None:
            journal_entry_ids.append(proposal.journal_entry_id)

    customers = await _customer_names(session, org_id, customer_ids)
    products = await ledger_service.get_products(
        session, org_id, list(product_ids)
    )
    documents = await ledger_service.get_posting_documents(
        session, org_id, journal_entry_ids
    )

    results: list[CreditNoteOut] = []
    for proposal in proposals:
        payload = dict(proposal.payload or {})
        customer_id = _uuid_or_none(payload.get("customer_id"))
        document = documents.get(proposal.journal_entry_id)
        credit_note_id = (
            document.receipt_id
            if document is not None and document.receipt_type == "credit_note"
            else None
        )
        results.append(
            CreditNoteOut(
                id=proposal.id,
                proposal_type=proposal.proposal_type,
                title=proposal.title,
                status=proposal.status,
                amount=proposal.amount,
                currency=proposal.currency,
                effective_at=_datetime_or_none(payload.get("effective_at")),
                customer_id=customer_id,
                customer_name=customers.get(customer_id) if customer_id else None,
                original_receipt_id=_uuid_or_none(
                    payload.get("original_receipt_id")
                ),
                restock=bool(payload.get("restock", True)),
                refund_method=str(payload.get("refund_method", "cash")),
                cash_account_code=str(payload.get("cash_account_code", "1000")),
                notes=payload.get("notes"),
                memo=payload.get("memo"),
                lines=[
                    _credit_note_line(item, products)
                    for item in (payload.get("lines") or [])
                ],
                journal_entry_id=proposal.journal_entry_id,
                credit_note_id=credit_note_id,
                credit_note_number=document.receipt_number
                if credit_note_id is not None and document is not None
                else None,
                posted_at=proposal.posted_at,
                posting_error=dict(proposal.posting_error)
                if proposal.posting_error is not None
                else None,
                created_at=proposal.created_at,
                updated_at=proposal.updated_at,
            )
        )
    return results


async def create_sale(
    session: AsyncSession, data: SaleCreate, actor: TokenPayload
) -> SaleCreateOut:
    customer = await customer_service.get_customer(
        session, actor, data.customer_id
    )
    _require_sale_customer(customer, data.customer_id)

    product_ids = [line.product_id for line in data.lines]
    products = await _require_sale_products(
        session, actor.org_id, product_ids
    )

    for line in data.lines:
        level = await ledger_service.get_stock_level(
            session, actor.org_id, line.product_id
        )
        available = level.quantity_on_hand if level is not None else ZERO
        if available < line.quantity:
            raise DomainValidationError(
                "Insufficient stock for one or more products",
                details={
                    "product_id": str(line.product_id),
                    "requested": str(line.quantity),
                    "available": str(available),
                },
            )

    total = ZERO
    payload_lines: list[dict] = []
    for line in data.lines:
        product = products[line.product_id]
        unit_price = (
            line.unit_price
            if line.unit_price is not None
            else product.current_sale_price
        )
        unit_cost = product.current_cost
        total += line.quantity * unit_price
        payload_line: dict = {
            "product_id": str(line.product_id),
            "quantity": str(line.quantity),
            "unit_price": str(unit_price),
            "unit_cost": str(unit_cost),
        }
        if line.description:
            payload_line["description"] = line.description
        payload_lines.append(payload_line)

    amount = _proposal_amount(total)

    payment: dict = {
        "method": data.payment.method,
        "cash_account_code": data.payment.cash_account_code,
    }
    if data.payment.due_on is not None:
        payment["due_on"] = data.payment.due_on.isoformat()
    if data.payment.issue_receipt is not None:
        payment["issue_receipt"] = data.payment.issue_receipt
    if data.payment.receipt_notes:
        payment["receipt_notes"] = data.payment.receipt_notes

    payload: dict = {
        "customer_id": str(data.customer_id),
        "currency": data.currency,
        "effective_at": _utc_iso(data.effective_at),
        "lines": payload_lines,
        "payment": payment,
    }
    if data.memo:
        payload["memo"] = data.memo

    proposal, created = await proposal_service.create_proposal(
        session,
        ProposalCreate(
            proposal_type="sale",
            title=f"Sale - {data.currency} {amount:.2f}"[:200],
            payload=payload,
            amount=amount,
            currency=data.currency,
            expires_in_hours=data.expires_in_hours,
            idempotency_key=data.idempotency_key,
        ),
        actor,
    )
    sale = (await _serialize_sales(session, actor.org_id, [proposal]))[0]
    return SaleCreateOut(sale=sale, created=created)


async def list_sales(
    session: AsyncSession,
    actor: TokenPayload,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[SaleOut], int]:
    rows, total = await proposal_service.list_proposals(
        session,
        actor,
        status=status,
        proposal_type="sale",
        limit=limit,
        offset=offset,
    )
    return await _serialize_sales(session, actor.org_id, rows), total


async def get_sale(
    session: AsyncSession, actor: TokenPayload, sale_id: uuid.UUID
) -> SaleOut:
    proposal = await proposal_service.get_proposal(session, actor, sale_id)
    if proposal.proposal_type.strip().lower() != "sale":
        raise NotFoundError("Sale not found")
    return (await _serialize_sales(session, actor.org_id, [proposal]))[0]


async def create_credit_note(
    session: AsyncSession, data: CreditNoteCreate, actor: TokenPayload
) -> CreditNoteCreateOut:
    receipt = None
    customer_id = data.customer_id
    currency = data.currency
    if data.original_receipt_id is not None:
        receipt = await ledger_service.get_receipt(
            session, actor.org_id, data.original_receipt_id
        )
        if receipt is None:
            raise DomainValidationError(
                "The original receipt does not exist in this organisation",
                details={"receipt_id": str(data.original_receipt_id)},
            )
        if receipt.receipt_type != "receipt":
            raise DomainValidationError(
                "A credit note must reference a receipt, not another credit note",
                details={"receipt_id": str(data.original_receipt_id)},
            )
        if customer_id is not None and customer_id != receipt.customer_id:
            raise DomainValidationError(
                "The credit note customer does not match the receipt",
                details={
                    "customer_id": str(customer_id),
                    "receipt_customer_id": str(receipt.customer_id),
                },
            )
        if currency is not None and currency != receipt.currency_code:
            raise DomainValidationError(
                "The credit note currency does not match the receipt",
                details={
                    "currency": currency,
                    "receipt_currency": receipt.currency_code,
                },
            )
        customer_id = customer_id or receipt.customer_id
        currency = currency or receipt.currency_code

    if customer_id is None:
        raise DomainValidationError("A credit note requires a customer")
    if currency is None:
        raise DomainValidationError("A credit note requires a currency")
    customer = await customer_service.get_customer(session, actor, customer_id)
    _require_sale_customer(customer, customer_id)

    original_lines = None
    prior_quantities: dict[uuid.UUID, Decimal] = {}
    if receipt is not None:
        original_lines = await _original_sale_lines(
            session, actor.org_id, receipt
        )
        if original_lines is None:
            raise DomainValidationError(
                "The original receipt is not linked to a completed sale",
                details={"receipt_id": str(receipt.id)},
            )
        prior_quantities = await _prior_credit_quantities(
            session, actor.org_id, receipt.id
        )
        _validate_credit_lines_against_sale(
            original_lines, prior_quantities, data.lines
        )

    product_ids = [
        line.product_id
        for line in data.lines
        if line.product_id is not None
    ]
    products = await ledger_service.get_products(
        session, actor.org_id, product_ids
    )
    missing = [str(pid) for pid in product_ids if pid not in products]
    if missing:
        raise DomainValidationError(
            "One or more products do not exist",
            details={"missing": missing},
        )

    total = ZERO
    payload_lines: list[dict] = []
    for line in data.lines:
        product = products.get(line.product_id) if line.product_id else None
        unit_cost = None
        if data.restock:
            if original_lines is not None and line.product_id is not None:
                unit_cost = original_lines[line.product_id][1]
            elif product is not None:
                unit_cost = product.current_cost
        total += line.quantity * line.unit_price
        payload_line: dict = {
            "quantity": str(line.quantity),
            "unit_price": str(line.unit_price),
        }
        if line.product_id is not None:
            payload_line["product_id"] = str(line.product_id)
        if unit_cost is not None:
            payload_line["unit_cost"] = str(unit_cost)
        if line.description:
            payload_line["description"] = line.description
        payload_lines.append(payload_line)

    amount = _proposal_amount(total)
    if receipt is not None:
        credited = await ledger_service.receipt_credit_note_total(
            session, actor.org_id, receipt.id
        )
        if credited + amount > receipt.total_amount:
            raise DomainValidationError(
                "The credit note exceeds the uncredited receipt value",
                details={
                    "receipt_total": str(receipt.total_amount),
                    "already_credited": str(credited),
                    "requested": str(amount),
                },
            )

    payload: dict = {
        "customer_id": str(customer_id),
        "currency": currency,
        "effective_at": _utc_iso(data.effective_at),
        "lines": payload_lines,
        "restock": data.restock,
        "refund_method": data.refund_method,
        "cash_account_code": data.cash_account_code,
    }
    if data.original_receipt_id is not None:
        payload["original_receipt_id"] = str(data.original_receipt_id)
    if data.notes:
        payload["notes"] = data.notes
    if data.memo:
        payload["memo"] = data.memo

    proposal, created = await proposal_service.create_proposal(
        session,
        ProposalCreate(
            proposal_type="credit_note",
            title=f"Credit note - {currency} {amount:.2f}"[:200],
            payload=payload,
            amount=amount,
            currency=currency,
            expires_in_hours=data.expires_in_hours,
            idempotency_key=data.idempotency_key,
        ),
        actor,
    )
    credit_note = (
        await _serialize_credit_notes(session, actor.org_id, [proposal])
    )[0]
    return CreditNoteCreateOut(credit_note=credit_note, created=created)


async def list_credit_notes(
    session: AsyncSession,
    actor: TokenPayload,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[CreditNoteOut], int]:
    rows, total = await proposal_service.list_proposals(
        session,
        actor,
        status=status,
        proposal_type="credit_note",
        limit=limit,
        offset=offset,
    )
    return await _serialize_credit_notes(session, actor.org_id, rows), total


async def get_credit_note(
    session: AsyncSession, actor: TokenPayload, credit_note_id: uuid.UUID
) -> CreditNoteOut:
    proposal = await proposal_service.get_proposal(
        session, actor, credit_note_id
    )
    if proposal.proposal_type.strip().lower() != "credit_note":
        raise NotFoundError("Credit note not found")
    return (
        await _serialize_credit_notes(session, actor.org_id, [proposal])
    )[0]
