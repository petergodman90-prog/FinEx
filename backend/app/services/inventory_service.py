"""Inventory stock reads, opening stock, and stock adjustments."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TokenPayload
from app.models.proposal import Proposal
from app.schemas.inventory import (
    OpeningStockCreate,
    StockAdjustmentCreate,
    StockLevelOut,
    StockMovementOut,
    StockProposalOut,
    StockValuationOut,
    StockValuationRowOut,
)
from app.schemas.proposal import ProposalCreate, ProposalOut
from app.services import ledger_service, proposal_service
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


def serialize_stock_level(level: ledger_service.StockLevel) -> StockLevelOut:
    return StockLevelOut(
        product_id=level.product_id,
        sku=level.sku,
        name=level.name,
        quantity_on_hand=level.quantity_on_hand,
        reorder_point=level.reorder_point,
        needs_reorder=level.needs_reorder,
    )


def serialize_movement(movement: ledger_service.StockMovement) -> StockMovementOut:
    return StockMovementOut(
        id=movement.id,
        product_id=movement.product_id,
        sku=movement.sku,
        name=movement.name,
        movement_type=movement.movement_type,
        quantity_delta=movement.quantity_delta,
        unit_cost=movement.unit_cost,
        source_type=movement.source_type,
        source_id=movement.source_id,
        journal_entry_id=movement.journal_entry_id,
        occurred_at=movement.occurred_at,
        note=movement.note,
        created_at=movement.created_at,
    )


def serialize_stock_proposal(
    proposal: Proposal, *, created: bool
) -> StockProposalOut:
    return StockProposalOut(
        proposal=proposal_service.serialize_proposal(proposal),
        created=created,
    )


async def list_stock_levels(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    q: str | None = None,
    include_inactive: bool = False,
    needs_reorder: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[StockLevelOut], int]:
    rows, total = await ledger_service.list_stock_levels_page(
        session,
        org_id,
        include_inactive=include_inactive,
        only_needs_reorder=needs_reorder,
        search=q,
        limit=limit,
        offset=offset,
    )
    return [serialize_stock_level(row) for row in rows], total


async def get_stock_level(
    session: AsyncSession, org_id: uuid.UUID, product_id: uuid.UUID
) -> StockLevelOut:
    level = await ledger_service.get_stock_level(session, org_id, product_id)
    if level is None:
        raise NotFoundError("Product not found")
    return serialize_stock_level(level)


async def get_stock_valuation(
    session: AsyncSession, org_id: uuid.UUID
) -> StockValuationOut:
    report = await ledger_service.stock_valuation(session, org_id)
    return StockValuationOut(
        rows=[
            StockValuationRowOut(
                product_id=row.product_id,
                sku=row.sku,
                name=row.name,
                quantity_on_hand=row.quantity_on_hand,
                book_value=row.book_value,
                average_unit_cost=row.average_unit_cost,
            )
            for row in report.rows
        ],
        total_book_value=report.total_book_value,
        inventory_account_balance=report.inventory_account_balance,
        is_reconciled=report.is_reconciled,
    )


async def list_stock_movements(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    product_id: uuid.UUID | None = None,
    movement_type: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[StockMovementOut], int]:
    if (
        since is not None
        and until is not None
        and _as_utc(since) >= _as_utc(until)
    ):
        raise DomainValidationError("until must be later than since")
    rows, total = await ledger_service.list_stock_movements(
        session,
        org_id,
        product_id=product_id,
        movement_type=movement_type,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return [serialize_movement(row) for row in rows], total


async def _require_products(
    session: AsyncSession, org_id: uuid.UUID, product_ids: list[uuid.UUID]
) -> dict[uuid.UUID, ledger_service.Product]:
    products = await ledger_service.get_products(session, org_id, product_ids)
    missing = [
        str(product_id)
        for product_id in product_ids
        if product_id not in products
    ]
    inactive = [
        str(product_id)
        for product_id in product_ids
        if product_id in products and not products[product_id].is_active
    ]
    if missing or inactive:
        raise DomainValidationError(
            "One or more products do not exist or are inactive",
            details={"missing": missing, "inactive": inactive},
        )
    return products


def _proposal_amount(total_cost: Decimal) -> Decimal:
    total_cost = total_cost.quantize(FOUR_PLACES)
    if total_cost <= ZERO:
        raise DomainValidationError(
            "The proposal total must be positive",
            details={"total_cost": str(total_cost)},
        )
    amount = total_cost.quantize(TWO_PLACES)
    if amount != total_cost:
        raise DomainValidationError(
            "The proposal total must have at most two decimal places",
            details={"total_cost": str(total_cost)},
        )
    return amount


async def create_opening_stock(
    session: AsyncSession, data: OpeningStockCreate, actor: TokenPayload
) -> StockProposalOut:
    product_ids = [line.product_id for line in data.lines]
    products = await _require_products(session, actor.org_id, product_ids)

    total_cost = ZERO
    lines: list[dict] = []
    for line in data.lines:
        unit_cost = (
            line.unit_cost
            if line.unit_cost is not None
            else products[line.product_id].current_cost
        )
        total_cost += line.quantity * unit_cost
        payload_line = {
            "product_id": str(line.product_id),
            "quantity": str(line.quantity),
            "unit_cost": str(unit_cost),
        }
        if line.description:
            payload_line["description"] = line.description
        lines.append(payload_line)

    amount = _proposal_amount(total_cost)
    payload: dict = {
        "effective_at": _utc_iso(data.effective_at),
        "lines": lines,
    }
    if data.memo:
        payload["memo"] = data.memo

    proposal, created = await proposal_service.create_proposal(
        session,
        ProposalCreate(
            proposal_type="opening_stock",
            title=(data.memo or "Opening stock")[:200],
            payload=payload,
            amount=amount,
            expires_in_hours=data.expires_in_hours,
            idempotency_key=data.idempotency_key,
        ),
        actor,
    )
    return serialize_stock_proposal(proposal, created=created)


async def create_stock_adjustment(
    session: AsyncSession, data: StockAdjustmentCreate, actor: TokenPayload
) -> StockProposalOut:
    product_ids = [line.product_id for line in data.lines]
    products = await _require_products(session, actor.org_id, product_ids)

    total_cost = ZERO
    lines: list[dict] = []
    for line in data.lines:
        unit_cost = (
            line.unit_cost
            if line.unit_cost is not None
            else products[line.product_id].current_cost
        )
        if data.direction == "out":
            available = await ledger_service.stock_quantity(
                session, actor.org_id, line.product_id
            )
            if line.quantity > available:
                raise DomainValidationError(
                    "Insufficient stock for the adjustment",
                    details={
                        "product_id": str(line.product_id),
                        "requested": str(line.quantity),
                        "available": str(available),
                    },
                )
        total_cost += line.quantity * unit_cost
        payload_line = {
            "product_id": str(line.product_id),
            "quantity": str(line.quantity),
            "unit_cost": str(unit_cost),
        }
        if line.description:
            payload_line["description"] = line.description
        lines.append(payload_line)

    amount = _proposal_amount(total_cost)
    payload: dict = {
        "direction": data.direction,
        "effective_at": _utc_iso(data.effective_at),
        "reason": data.reason,
        "lines": lines,
    }
    if data.memo:
        payload["memo"] = data.memo

    proposal, created = await proposal_service.create_proposal(
        session,
        ProposalCreate(
            proposal_type="stock_adjustment",
            title=data.reason[:200],
            payload=payload,
            amount=amount,
            expires_in_hours=data.expires_in_hours,
            idempotency_key=data.idempotency_key,
        ),
        actor,
    )
    return serialize_stock_proposal(proposal, created=created)


async def list_stock_proposals(
    session: AsyncSession,
    actor: TokenPayload,
    *,
    proposal_type: str,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[ProposalOut], int]:
    rows, total = await proposal_service.list_proposals(
        session,
        actor,
        proposal_type=proposal_type,
        limit=limit,
        offset=offset,
    )
    return [proposal_service.serialize_proposal(row) for row in rows], total


async def get_stock_proposal(
    session: AsyncSession,
    actor: TokenPayload,
    proposal_id: uuid.UUID,
    *,
    proposal_type: str,
) -> ProposalOut:
    proposal = await proposal_service.get_proposal(session, actor, proposal_id)
    if proposal.proposal_type.strip().lower() != proposal_type:
        raise NotFoundError("Stock proposal not found")
    return proposal_service.serialize_proposal(proposal)
