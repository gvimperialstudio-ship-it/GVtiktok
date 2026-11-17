"""Atomic stock decrement.

The balance update is one conditional statement:

    UPDATE stock_balances
    SET qty_available = qty_available - :qty,
        qty_on_hand = qty_on_hand - :qty
    WHERE warehouse_id = :warehouse_id
      AND sku_id = :sku_id
      AND qty_available >= :qty
    RETURNING qty_available

Kit components are decremented in the same transaction, in SKU id order, so two
concurrent orders cannot deadlock or drive a balance negative. Idempotency is
the ``processed_events`` unique key: a duplicate insert matches zero rows and
the transaction returns without touching stock.

PostgreSQL is the only stock store. This module does not talk to Redis.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Integer, Uuid, bindparam, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from erp.db.models import KitComponent, Order, OrderItem, ProcessedEvent, Sku, Warehouse
from erp.domain.enums import EVENT_STOCK_DECREMENT, OrderStatus, WarehouseType
from erp.domain.errors import (
    InsufficientStock,
    KitHasNoComponents,
    NestedKitNotSupported,
    SkuNotFound,
)
from erp.stock.availability import StockSyncTask, build_fanout_tasks

_DECREMENT_SQL = text(
    """
    UPDATE stock_balances
    SET qty_available = qty_available - :qty,
        qty_on_hand = qty_on_hand - :qty,
        updated_at = NOW()
    WHERE warehouse_id = :warehouse_id
      AND sku_id = :sku_id
      AND qty_available >= :qty
    RETURNING qty_available
    """
).bindparams(
    bindparam("qty", type_=Integer()),
    bindparam("warehouse_id", type_=Uuid()),
    bindparam("sku_id", type_=Uuid()),
)


@dataclass(frozen=True)
class OrderLine:
    sku_id: uuid.UUID
    quantity: int


@dataclass(frozen=True)
class StockDecrementEvent:
    channel: str
    external_event_id: str
    company_id: uuid.UUID
    external_order_id: str
    warehouse_id: uuid.UUID
    lines: tuple[OrderLine, ...]
    event_type: str = EVENT_STOCK_DECREMENT

    def as_payload(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "external_event_id": self.external_event_id,
            "company_id": str(self.company_id),
            "external_order_id": self.external_order_id,
            "warehouse_id": str(self.warehouse_id),
            "event_type": self.event_type,
            "lines": [
                {"sku_id": str(line.sku_id), "quantity": line.quantity} for line in self.lines
            ],
        }


def stock_event_from_payload(payload: dict[str, Any]) -> StockDecrementEvent:
    return StockDecrementEvent(
        channel=str(payload["channel"]),
        external_event_id=str(payload["external_event_id"]),
        company_id=uuid.UUID(str(payload["company_id"])),
        external_order_id=str(payload["external_order_id"]),
        warehouse_id=uuid.UUID(str(payload["warehouse_id"])),
        lines=tuple(
            OrderLine(sku_id=uuid.UUID(str(line["sku_id"])), quantity=int(line["quantity"]))
            for line in payload["lines"]
        ),
        event_type=str(payload.get("event_type", EVENT_STOCK_DECREMENT)),
    )


@dataclass(frozen=True)
class ApplyResult:
    status: str
    order_id: uuid.UUID | None
    changed_sku_ids: tuple[uuid.UUID, ...]
    fanout_tasks: tuple[StockSyncTask, ...]


class StockService:
    """Apply one marketplace stock event inside the caller's transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def apply_event(self, event: StockDecrementEvent) -> ApplyResult:
        self._validate(event)
        inserted = await self._mark_processed(event)
        if not inserted:
            return ApplyResult(
                status="duplicate",
                order_id=None,
                changed_sku_ids=(),
                fanout_tasks=(),
            )

        order = await self._insert_order(event)
        touches = await self._expand_lines(event.lines)
        # Lock rows in a stable order across concurrent transactions.
        for sku_id in sorted(touches):
            await self._conditional_decrement(event.warehouse_id, sku_id, touches[sku_id])
        for line in _merge_lines(event.lines):
            self.session.add(
                OrderItem(
                    id=uuid.uuid4(),
                    order_id=order.id,
                    sku_id=line.sku_id,
                    warehouse_id=event.warehouse_id,
                    quantity=line.quantity,
                )
            )

        changed = tuple(sorted(touches))
        tasks: tuple[StockSyncTask, ...] = ()
        warehouse = await self.session.get(Warehouse, event.warehouse_id)
        if warehouse is not None and warehouse.warehouse_type == WarehouseType.SHARED.value:
            tasks = tuple(await build_fanout_tasks(self.session, changed))
        return ApplyResult(
            status="applied",
            order_id=order.id,
            changed_sku_ids=changed,
            fanout_tasks=tasks,
        )

    def _validate(self, event: StockDecrementEvent) -> None:
        if not event.lines:
            raise ValueError("stock event has no lines")
        for line in event.lines:
            if line.quantity <= 0:
                raise ValueError("line quantity must be positive")

    async def _mark_processed(self, event: StockDecrementEvent) -> bool:
        stmt = (
            pg_insert(ProcessedEvent)
            .values(
                id=uuid.uuid4(),
                channel=event.channel,
                external_event_id=event.external_event_id,
                event_type=event.event_type,
                payload=event.as_payload(),
            )
            .on_conflict_do_nothing(constraint="uq_processed_events_channel_event")
            .returning(ProcessedEvent.id)
        )
        inserted_id = (await self.session.execute(stmt)).scalar_one_or_none()
        return inserted_id is not None

    async def _insert_order(self, event: StockDecrementEvent) -> Order:
        order = Order(
            id=uuid.uuid4(),
            channel=event.channel,
            company_id=event.company_id,
            external_order_id=event.external_order_id,
            status=OrderStatus.STOCK_DECREMENTED.value,
            warehouse_id=event.warehouse_id,
            raw_payload=event.as_payload(),
        )
        self.session.add(order)
        await self.session.flush()
        return order

    async def _expand_lines(self, lines: tuple[OrderLine, ...]) -> dict[uuid.UUID, int]:
        touches: dict[uuid.UUID, int] = {}
        for line in _merge_lines(lines):
            for sku_id, quantity in await self._expand_line(line):
                touches[sku_id] = touches.get(sku_id, 0) + quantity
        return touches

    async def _expand_line(self, line: OrderLine) -> list[tuple[uuid.UUID, int]]:
        sku = await self.session.get(Sku, line.sku_id)
        if sku is None:
            raise SkuNotFound(line.sku_id)
        if not sku.is_kit:
            return [(sku.id, line.quantity)]

        component_rows = (
            await self.session.execute(
                select(KitComponent)
                .where(KitComponent.kit_sku_id == sku.id)
                .order_by(KitComponent.component_sku_id.asc())
            )
        ).scalars().all()
        if not component_rows:
            raise KitHasNoComponents(sku.id)
        component_ids = [row.component_sku_id for row in component_rows]
        nested = (
            await self.session.execute(
                select(Sku.id).where(Sku.id.in_(component_ids), Sku.is_kit.is_(True))
            )
        ).all()
        if nested:
            raise NestedKitNotSupported(sku.id)
        return [
            (row.component_sku_id, row.quantity * line.quantity) for row in component_rows
        ]

    async def _conditional_decrement(
        self, warehouse_id: uuid.UUID, sku_id: uuid.UUID, quantity: int
    ) -> int:
        result = await self.session.execute(
            _DECREMENT_SQL,
            {"qty": quantity, "warehouse_id": warehouse_id, "sku_id": sku_id},
        )
        row = result.first()
        if row is None:
            raise InsufficientStock(sku_id, warehouse_id, quantity)
        return int(row[0])


def _merge_lines(lines: tuple[OrderLine, ...]) -> tuple[OrderLine, ...]:
    merged: dict[uuid.UUID, int] = {}
    for line in lines:
        merged[line.sku_id] = merged.get(line.sku_id, 0) + line.quantity
    return tuple(OrderLine(sku_id=sku_id, quantity=qty) for sku_id, qty in merged.items())
