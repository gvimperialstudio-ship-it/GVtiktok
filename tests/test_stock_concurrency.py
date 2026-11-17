"""Two concurrent orders for the last unit. Exactly one commits."""

import asyncio

from sqlalchemy import func, select

from erp.db.models import Order, ProcessedEvent, StockBalance
from erp.domain.enums import Channel
from erp.domain.errors import InsufficientStock
from erp.stock.service import ApplyResult, OrderLine, StockDecrementEvent, StockService


def _event(stock, event_id: str, order_id: str) -> StockDecrementEvent:
    return StockDecrementEvent(
        channel=Channel.MERCADO_LIVRE.value,
        external_event_id=event_id,
        company_id=stock.company_id,
        external_order_id=order_id,
        warehouse_id=stock.shared_warehouse_id,
        lines=(OrderLine(sku_id=stock.sku_id, quantity=1),),
    )


async def test_concurrent_decrements_let_exactly_one_order_take_the_last_unit(
    session_factory, seed
):
    stock = await seed.simple_stock(qty=1)
    started = asyncio.Event()

    async def attempt(event: StockDecrementEvent):
        # Both tasks block here so the two UPDATEs are in flight together.
        await started.wait()
        async with session_factory() as session:
            async with session.begin():
                return await StockService(session).apply_event(event)

    first = asyncio.create_task(_event_task(attempt, _event(stock, "evt-a", "ord-a")))
    second = asyncio.create_task(_event_task(attempt, _event(stock, "evt-b", "ord-b")))
    await asyncio.sleep(0)
    started.set()
    results = await asyncio.gather(first, second)

    applied = [item for item in results if isinstance(item, ApplyResult) and item.status == "applied"]
    rejected = [item for item in results if isinstance(item, InsufficientStock)]
    unexpected = [
        item
        for item in results
        if isinstance(item, Exception) and not isinstance(item, InsufficientStock)
    ]
    assert unexpected == []
    assert len(applied) == 1
    assert len(rejected) == 1

    async with session_factory() as session:
        balance = await session.scalar(
            select(StockBalance).where(
                StockBalance.warehouse_id == stock.shared_warehouse_id,
                StockBalance.sku_id == stock.sku_id,
            )
        )
        processed = await session.scalar(select(func.count()).select_from(ProcessedEvent))
        orders = await session.scalar(select(func.count()).select_from(Order))
    assert balance is not None
    assert balance.qty_available == 0
    assert balance.qty_on_hand == 0
    assert processed == 1
    assert orders == 1


async def _event_task(attempt, event):
    try:
        return await attempt(event)
    except InsufficientStock as exc:
        return exc
