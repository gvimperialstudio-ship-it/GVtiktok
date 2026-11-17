"""Shadow mode fans out one task per active non-Full listing and does not call HTTP."""

import json
import logging

import pytest

from erp.config import Settings
from erp.domain.enums import Channel, SyncStatus
from erp.queue.redis_queue import RedisTaskQueue
from erp.stock.service import OrderLine, StockDecrementEvent, StockService
from erp.workers.stock_sync import drain_stock_sync_queue, process_stock_sync_task
from erp.stock.availability import StockSyncTask


class SpyChannelClient:
    def __init__(self) -> None:
        self.commands = []

    async def send_stock_command(self, command) -> None:
        self.commands.append(command)


def _settings(database_url: str, redis_url: str, *, shadow_mode: bool) -> Settings:
    return Settings(
        database_url=database_url,
        redis_url=redis_url,
        shadow_mode=shadow_mode,
        secrets_provider="local",
        ops_bearer_token_ref="env:OPS_BEARER_TOKEN",
        webhook_hmac_secret_ref="env:WEBHOOK_HMAC_SECRET",
        webhook_url="",
    )


def _shadow_payloads(caplog: pytest.LogCaptureFixture) -> list[dict]:
    payloads = []
    prefix = "SHADOW_MODE would PUT "
    for record in caplog.records:
        if record.name == "erp.stock_sync" and record.message.startswith(prefix):
            payloads.append(json.loads(record.message[len(prefix) :]))
    return payloads


async def test_shadow_mode_fanout_logs_puts_and_excludes_full(
    session_factory, seed, redis_client, database_url, redis_url, caplog
):
    stock = await seed.simple_stock(qty=10, safety_threshold=2, full_qty=500)
    await seed.add_listing(
        sku_id=stock.sku_id,
        company_id=stock.company_id,
        channel=Channel.MERCADO_LIVRE.value,
        external_item_id="ML-ITEM",
        is_full=False,
        is_active=True,
        is_primary=True,
    )
    await seed.add_listing(
        sku_id=stock.sku_id,
        company_id=stock.company_id,
        channel=Channel.SHOPEE.value,
        external_item_id="SHP-ITEM",
        external_variation_id="VAR-1",
        is_full=False,
        is_active=True,
    )
    await seed.add_listing(
        sku_id=stock.sku_id,
        company_id=stock.company_id,
        channel=Channel.MERCADO_LIVRE.value,
        external_item_id="FULL-ITEM",
        is_full=True,
        is_active=True,
    )
    await seed.add_listing(
        sku_id=stock.sku_id,
        company_id=stock.company_id,
        channel=Channel.OWN_STORE.value,
        external_item_id="INACTIVE-ITEM",
        is_full=False,
        is_active=False,
    )

    event = StockDecrementEvent(
        channel=Channel.MERCADO_LIVRE.value,
        external_event_id="sale-1",
        company_id=stock.company_id,
        external_order_id="order-1",
        warehouse_id=stock.shared_warehouse_id,
        lines=(OrderLine(sku_id=stock.sku_id, quantity=1),),
    )
    async with session_factory() as session:
        async with session.begin():
            applied = await StockService(session).apply_event(event)

    assert applied.status == "applied"
    assert len(applied.fanout_tasks) == 2

    queue = RedisTaskQueue(redis_client)
    await queue.enqueue_many(applied.fanout_tasks)
    assert await queue.depth() == 2
    queued = await redis_client.lrange(queue.key, 0, -1)
    for raw in queued:
        payload = json.loads(raw)
        assert set(payload) == {"listing_link_id", "sku_id"}

    spy = SpyChannelClient()
    clients = {channel.value: spy for channel in Channel}
    caplog.set_level(logging.INFO, logger="erp.stock_sync")
    settings = _settings(database_url, redis_url, shadow_mode=True)
    drained = await drain_stock_sync_queue(
        queue=queue,
        session_factory=session_factory,
        settings=settings,
        channel_clients=clients,
    )
    assert len(drained) == 2
    assert spy.commands == []

    payloads = _shadow_payloads(caplog)
    assert len(payloads) == 2
    by_item = {item["body"]["external_item_id"]: item for item in payloads}
    assert set(by_item) == {"ML-ITEM", "SHP-ITEM"}
    for item in payloads:
        assert item["method"] == "PUT"
        assert item["body"]["action"] == "update"
        # 10 - 1 sold = 9 shared. Threshold 2. Full's 500 is not included.
        assert item["body"]["quantity"] == 7
    assert "FULL-ITEM" not in caplog.text
    assert "INACTIVE-ITEM" not in caplog.text

    async with session_factory() as session:
        from sqlalchemy import select

        from erp.db.models import ListingLink, StockBalance

        links = (await session.scalars(select(ListingLink))).all()
        full_balance = await session.scalar(
            select(StockBalance).where(StockBalance.warehouse_id == stock.full_warehouse_id)
        )
    by_external = {link.external_item_id: link for link in links}
    assert by_external["ML-ITEM"].sync_status == SyncStatus.SHADOW_LOGGED.value
    assert by_external["SHP-ITEM"].sync_status == SyncStatus.SHADOW_LOGGED.value
    assert by_external["ML-ITEM"].last_synced_qty is None
    assert by_external["SHP-ITEM"].last_synced_qty is None
    assert by_external["FULL-ITEM"].sync_status == SyncStatus.PENDING.value
    assert full_balance is not None
    assert full_balance.qty_available == 500

    caplog.clear()
    pause_event = StockDecrementEvent(
        channel=Channel.MERCADO_LIVRE.value,
        external_event_id="sale-2",
        company_id=stock.company_id,
        external_order_id="order-2",
        warehouse_id=stock.shared_warehouse_id,
        lines=(OrderLine(sku_id=stock.sku_id, quantity=7),),
    )
    async with session_factory() as session:
        async with session.begin():
            paused = await StockService(session).apply_event(pause_event)
    await queue.enqueue_many(paused.fanout_tasks)
    await drain_stock_sync_queue(
        queue=queue,
        session_factory=session_factory,
        settings=settings,
        channel_clients=clients,
    )
    pause_payloads = _shadow_payloads(caplog)
    assert len(pause_payloads) == 2
    assert {item["body"]["action"] for item in pause_payloads} == {"pause"}
    assert {item["body"]["quantity"] for item in pause_payloads} == {0}
    assert spy.commands == []


async def test_disabled_shadow_mode_calls_the_channel_client(
    session_factory, seed, database_url, redis_url, caplog
):
    stock = await seed.simple_stock(qty=10, safety_threshold=2)
    link_id = await seed.add_listing(
        sku_id=stock.sku_id,
        company_id=stock.company_id,
        channel=Channel.SHOPEE.value,
        external_item_id="SHP-LIVE",
        is_full=False,
        is_active=True,
    )
    spy = SpyChannelClient()
    caplog.set_level(logging.INFO, logger="erp.stock_sync")
    async with session_factory() as session:
        command = await process_stock_sync_task(
            session,
            StockSyncTask(listing_link_id=link_id, sku_id=stock.sku_id),
            settings=_settings(database_url, redis_url, shadow_mode=False),
            channel_clients={Channel.SHOPEE.value: spy},
            rate_limiter=None,
        )
    assert command is not None
    assert len(spy.commands) == 1
    assert spy.commands[0].action == "update"
    assert spy.commands[0].quantity == 8
    assert spy.commands[0].method == "PUT"
    assert "SHADOW_MODE would PUT" not in caplog.text

    async with session_factory() as session:
        from erp.db.models import ListingLink

        link = await session.get(ListingLink, link_id)
    assert link is not None
    assert link.last_synced_qty == 8
    assert link.sync_status == SyncStatus.SYNCED.value
