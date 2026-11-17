"""Sellable quantity and listing fan-out.

Shared availability reads ``warehouse_type = 'shared'`` only. Full-ML balances
are a different warehouse and are never added in.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from erp.db.models import KitComponent, ListingLink, StockBalance, Warehouse
from erp.domain.enums import WarehouseType


@dataclass(frozen=True)
class StockSyncTask:
    """Pointer to a listing. The worker reloads quantity from PostgreSQL.

    Redis may carry this id pair. It must not be treated as the stock balance.
    """

    listing_link_id: uuid.UUID
    sku_id: uuid.UUID


async def shared_qty_available(session: AsyncSession, sku_id: uuid.UUID) -> int:
    """Units of ``sku_id`` in shared warehouses only."""

    stmt = (
        select(func.coalesce(func.sum(StockBalance.qty_available), 0))
        .select_from(StockBalance)
        .join(Warehouse, Warehouse.id == StockBalance.warehouse_id)
        .where(
            StockBalance.sku_id == sku_id,
            Warehouse.warehouse_type == WarehouseType.SHARED.value,
        )
    )
    return int((await session.execute(stmt)).scalar_one())


async def full_qty_available(
    session: AsyncSession, *, company_id: uuid.UUID, sku_id: uuid.UUID
) -> tuple[str | None, int | None]:
    """Isolated Full balance for one CNPJ. Does not look at shared stock."""

    stmt = (
        select(Warehouse.code, StockBalance.qty_available)
        .join(StockBalance, StockBalance.warehouse_id == Warehouse.id)
        .where(
            Warehouse.company_id == company_id,
            Warehouse.warehouse_type == WarehouseType.FULL_ML.value,
            StockBalance.sku_id == sku_id,
        )
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        return None, None
    return str(row[0]), int(row[1])


async def sellable_qty(session: AsyncSession, sku_id: uuid.UUID, *, is_kit: bool) -> int:
    """How many of this SKU can be sold from shared stock.

    A kit's sellable count is the number of complete kits that fit in component
    stock. Component balances are shared-warehouse rows only.
    """

    if not is_kit:
        return await shared_qty_available(session, sku_id)

    components = (
        await session.execute(
            select(KitComponent)
            .where(KitComponent.kit_sku_id == sku_id)
            .order_by(KitComponent.component_sku_id.asc())
        )
    ).scalars().all()
    if not components:
        return 0
    sellable: int | None = None
    for component in components:
        available = await shared_qty_available(session, component.component_sku_id)
        kits = available // component.quantity
        sellable = kits if sellable is None else min(sellable, kits)
    return 0 if sellable is None else sellable


async def build_fanout_tasks(
    session: AsyncSession, changed_sku_ids: list[uuid.UUID] | tuple[uuid.UUID, ...]
) -> list[StockSyncTask]:
    """One task per active, non-Full listing of the changed SKU and its parent kits.

    A Full listing is never enqueued. A decrement that only touched a Full
    warehouse should pass an empty ``changed_sku_ids`` from the caller; this
    function itself also filters ``is_full``.
    """

    if not changed_sku_ids:
        return []
    parent_kits = (
        await session.execute(
            select(KitComponent.kit_sku_id).where(
                KitComponent.component_sku_id.in_(tuple(changed_sku_ids))
            )
        )
    ).scalars().all()
    sku_ids = set(changed_sku_ids) | set(parent_kits)
    links = (
        await session.execute(
            select(ListingLink)
            .where(
                ListingLink.sku_id.in_(sku_ids),
                ListingLink.is_active.is_(True),
                ListingLink.is_full.is_(False),
            )
            .order_by(ListingLink.id.asc())
        )
    ).scalars().all()
    return [
        StockSyncTask(listing_link_id=link.id, sku_id=link.sku_id) for link in links
    ]
