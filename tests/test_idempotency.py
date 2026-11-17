"""A replayed marketplace event must not move stock twice."""

from sqlalchemy import func, select

from erp.db.models import Order, ProcessedEvent, StockBalance
from erp.domain.enums import Channel
from erp.stock.service import OrderLine, StockDecrementEvent, StockService


async def _apply(session_factory, event: StockDecrementEvent):
    async with session_factory() as session:
        async with session.begin():
            return await StockService(session).apply_event(event)


async def test_duplicate_event_decrements_once(session_factory, seed):
    stock = await seed.simple_stock(qty=5)
    event = StockDecrementEvent(
        channel=Channel.SHOPEE.value,
        external_event_id="evt-same",
        company_id=stock.company_id,
        external_order_id="ord-1",
        warehouse_id=stock.shared_warehouse_id,
        lines=(OrderLine(sku_id=stock.sku_id, quantity=2),),
    )

    first = await _apply(session_factory, event)
    second = await _apply(session_factory, event)

    assert first.status == "applied"
    assert second.status == "duplicate"
    assert second.fanout_tasks == ()

    async with session_factory() as session:
        balance = await session.scalar(
            select(StockBalance).where(StockBalance.sku_id == stock.sku_id)
        )
        processed = await session.scalar(select(func.count()).select_from(ProcessedEvent))
        orders = await session.scalar(select(func.count()).select_from(Order))
    assert balance is not None
    assert balance.qty_available == 3
    assert balance.qty_on_hand == 3
    assert processed == 1
    assert orders == 1
