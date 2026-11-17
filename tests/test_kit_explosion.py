"""A kit order decrements components in one transaction, or none of them."""

from sqlalchemy import func, select

from erp.db.models import OrderItem, ProcessedEvent, StockBalance
from erp.domain.enums import Channel
from erp.domain.errors import InsufficientStock
from erp.stock.service import OrderLine, StockDecrementEvent, StockService


def _event(kit, *, event_id: str, order_id: str, quantity: int) -> StockDecrementEvent:
    return StockDecrementEvent(
        channel=Channel.OWN_STORE.value,
        external_event_id=event_id,
        company_id=kit.company_id,
        external_order_id=order_id,
        warehouse_id=kit.shared_warehouse_id,
        lines=(OrderLine(sku_id=kit.kit_sku_id, quantity=quantity),),
    )


async def _available(session_factory, warehouse_id, sku_id) -> int | None:
    async with session_factory() as session:
        balance = await session.scalar(
            select(StockBalance).where(
                StockBalance.warehouse_id == warehouse_id,
                StockBalance.sku_id == sku_id,
            )
        )
    if balance is None:
        return None
    return balance.qty_available


async def test_kit_explodes_into_components_and_rolls_back_together(session_factory, seed):
    # Kit = 2 discos + 1 pastilha. Stock covers 3 kits and not 5.
    kit = await seed.kit_stock(qty_a=10, qty_b=4, per_a=2, per_b=1)
    kit_listing = await seed.add_listing(
        sku_id=kit.kit_sku_id,
        company_id=kit.company_id,
        channel=Channel.MERCADO_LIVRE.value,
        external_item_id="KIT-ITEM",
        is_full=False,
        is_active=True,
    )
    await seed.add_listing(
        sku_id=kit.kit_sku_id,
        company_id=kit.company_id,
        channel=Channel.MERCADO_LIVRE.value,
        external_item_id="KIT-FULL",
        is_full=True,
        is_active=True,
    )

    async with session_factory() as session:
        async with session.begin():
            applied = await StockService(session).apply_event(
                _event(kit, event_id="kit-ok", order_id="order-ok", quantity=3)
            )

    assert applied.status == "applied"
    assert await _available(session_factory, kit.shared_warehouse_id, kit.component_a_id) == 4
    assert await _available(session_factory, kit.shared_warehouse_id, kit.component_b_id) == 1
    assert await _available(session_factory, kit.shared_warehouse_id, kit.kit_sku_id) is None
    assert [task.listing_link_id for task in applied.fanout_tasks] == [kit_listing]

    try:
        async with session_factory() as session:
            async with session.begin():
                await StockService(session).apply_event(
                    _event(kit, event_id="kit-fail", order_id="order-fail", quantity=2)
                )
    except InsufficientStock:
        pass
    else:
        raise AssertionError("expected the second kit order to be rejected")

    assert await _available(session_factory, kit.shared_warehouse_id, kit.component_a_id) == 4
    assert await _available(session_factory, kit.shared_warehouse_id, kit.component_b_id) == 1

    async with session_factory() as session:
        failed = await session.scalar(
            select(func.count())
            .select_from(ProcessedEvent)
            .where(ProcessedEvent.external_event_id == "kit-fail")
        )
        items = (await session.scalars(select(OrderItem))).all()
    assert failed == 0
    assert len(items) == 1
    assert items[0].sku_id == kit.kit_sku_id
    assert items[0].quantity == 3
