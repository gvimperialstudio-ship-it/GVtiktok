"""Stock sync worker.

On each task the worker reads the shared balance from PostgreSQL, applies the
safety threshold, and either logs the PUT (shadow mode, the default) or calls
the channel client. Full listings are skipped. Redis is only the queue that
delivered the listing id.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from erp.channels.base import ChannelClient, ListingStockCommand
from erp.config import Settings
from erp.db.models import Company, ListingLink, Sku
from erp.domain.enums import EVENT_STOCK_SYNC, SyncStatus
from erp.queue.rate_limit import RedisRateLimiter, requests_per_second
from erp.queue.redis_queue import RedisTaskQueue
from erp.stock.availability import StockSyncTask, sellable_qty
from erp.stock.policy import decide_listing_action

logger = logging.getLogger("erp.stock_sync")


async def process_stock_sync_task(
    session: AsyncSession,
    task: StockSyncTask,
    *,
    settings: Settings,
    channel_clients: Mapping[str, ChannelClient],
    rate_limiter: RedisRateLimiter | None = None,
) -> ListingStockCommand | None:
    link = await session.get(ListingLink, task.listing_link_id)
    if link is None or not link.is_active or link.is_full:
        return None
    sku = await session.get(Sku, link.sku_id)
    company = await session.get(Company, link.company_id)
    if sku is None or company is None:
        return None

    # Shared warehouse only. Full balances are not part of this number.
    sellable = await sellable_qty(session, sku.id, is_kit=sku.is_kit)
    action, quantity = decide_listing_action(
        sellable_qty=sellable, safety_threshold=sku.safety_threshold
    )
    command = ListingStockCommand(
        method="PUT",
        channel=link.channel,
        company_cnpj=company.cnpj,
        external_item_id=link.external_item_id,
        external_variation_id=link.external_variation_id,
        action=action,
        quantity=quantity,
    )

    if settings.shadow_mode:
        logger.info("SHADOW_MODE would PUT %s", command.to_log_json())
        link.sync_status = SyncStatus.SHADOW_LOGGED.value
        await session.commit()
        return command

    client = channel_clients[link.channel]
    if rate_limiter is not None:
        limit = requests_per_second(company.rate_limit_config or {}, link.channel)
        await rate_limiter.acquire(company.cnpj, link.channel, limit)
    await client.send_stock_command(command)
    link.last_synced_qty = quantity
    link.sync_status = (
        SyncStatus.PAUSED.value if action == "pause" else SyncStatus.SYNCED.value
    )
    link.last_synced_at = datetime.now(timezone.utc)
    await session.commit()
    return command


async def drain_stock_sync_queue(
    *,
    queue: RedisTaskQueue,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    channel_clients: Mapping[str, ChannelClient],
    rate_limiter: RedisRateLimiter | None = None,
) -> list[ListingStockCommand | None]:
    """Process every task currently queued. Does not block waiting for new ones."""

    results: list[ListingStockCommand | None] = []
    while True:
        task = await queue.try_dequeue()
        if task is None:
            return results
        async with session_factory() as session:
            results.append(
                await process_stock_sync_task(
                    session,
                    task,
                    settings=settings,
                    channel_clients=channel_clients,
                    rate_limiter=rate_limiter,
                )
            )


def sync_failure_payload(task: StockSyncTask) -> dict[str, str]:
    return {
        "listing_link_id": str(task.listing_link_id),
        "sku_id": str(task.sku_id),
        "event_type": EVENT_STOCK_SYNC,
    }
