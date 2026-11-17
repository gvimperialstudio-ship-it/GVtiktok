"""Redis list used only as a queue of listing ids.

Payloads are ``{"listing_link_id", "sku_id"}``. Quantity is loaded from
PostgreSQL when the worker runs. Do not put ``qty_available`` in this queue
and then trust it: a later order may have changed the balance.
"""

from __future__ import annotations

import json
import uuid

from redis.asyncio import Redis

from erp.domain.enums import STOCK_SYNC_QUEUE_KEY
from erp.stock.availability import StockSyncTask


class RedisTaskQueue:
    def __init__(self, redis: Redis, key: str = STOCK_SYNC_QUEUE_KEY) -> None:
        self.redis = redis
        self.key = key

    async def enqueue_many(self, tasks: list[StockSyncTask] | tuple[StockSyncTask, ...]) -> None:
        if not tasks:
            return
        payloads = [
            json.dumps(
                {
                    "listing_link_id": str(task.listing_link_id),
                    "sku_id": str(task.sku_id),
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            for task in tasks
        ]
        await self.redis.rpush(self.key, *payloads)

    async def try_dequeue(self) -> StockSyncTask | None:
        raw = await self.redis.lpop(self.key)
        if raw is None:
            return None
        return _parse(raw)

    async def dequeue(self, timeout: int = 5) -> StockSyncTask | None:
        item = await self.redis.blpop(self.key, timeout=timeout)
        if not item:
            return None
        _key, raw = item
        return _parse(raw)

    async def depth(self) -> int:
        return int(await self.redis.llen(self.key))


def _parse(raw: str | bytes) -> StockSyncTask:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    data = json.loads(raw)
    return StockSyncTask(
        listing_link_id=uuid.UUID(data["listing_link_id"]),
        sku_id=uuid.UUID(data["sku_id"]),
    )
