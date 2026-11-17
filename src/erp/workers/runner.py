"""Long-running stock sync consumer."""

from __future__ import annotations

import asyncio
import logging

from redis.asyncio import Redis

from erp.channels.stubs import stub_clients
from erp.config import Settings, get_settings
from erp.db.models import DlqEvent, ListingLink
from erp.db.session import create_engine, create_session_factory
from erp.domain.enums import EVENT_STOCK_SYNC
from erp.queue.rate_limit import RedisRateLimiter
from erp.queue.redis_queue import RedisTaskQueue
from erp.workers.stock_sync import process_stock_sync_task, sync_failure_payload

logger = logging.getLogger("erp.worker")


async def run_worker(settings: Settings, stop: asyncio.Event | None = None) -> None:
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    queue = RedisTaskQueue(redis)
    limiter = RedisRateLimiter(redis)
    clients = stub_clients()
    logger.info("stock sync worker started shadow_mode=%s", settings.shadow_mode)
    try:
        while stop is None or not stop.is_set():
            task = await queue.dequeue(timeout=1)
            if task is None:
                continue
            async with session_factory() as session:
                try:
                    await process_stock_sync_task(
                        session,
                        task,
                        settings=settings,
                        channel_clients=clients,
                        rate_limiter=None if settings.shadow_mode else limiter,
                    )
                except Exception as exc:
                    await session.rollback()
                    await _record_sync_failure(session_factory, task, exc)
                    logger.exception("stock sync task failed listing=%s", task.listing_link_id)
    finally:
        await redis.aclose()
        await engine.dispose()


async def _record_sync_failure(session_factory, task, exc: Exception) -> None:
    import uuid
    from datetime import datetime, timezone

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    message = f"{type(exc).__name__}: {exc}"
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        async with session.begin():
            link = await session.get(ListingLink, task.listing_link_id)
            if link is None:
                return
            external_event_id = f"stock_sync:{task.listing_link_id}"
            stmt = (
                pg_insert(DlqEvent)
                .values(
                    id=uuid.uuid4(),
                    channel=link.channel,
                    external_event_id=external_event_id,
                    event_type=EVENT_STOCK_SYNC,
                    payload=sync_failure_payload(task),
                    error_message=message,
                    attempt_count=1,
                    status="pending",
                    last_attempt_at=now,
                )
                .on_conflict_do_update(
                    constraint="uq_dlq_events_channel_event",
                    set_={
                        "attempt_count": DlqEvent.attempt_count + 1,
                        "error_message": message,
                        "status": "pending",
                        "last_attempt_at": now,
                        "updated_at": now,
                    },
                )
            )
            await session.execute(stmt)


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    asyncio.run(run_worker(settings))


if __name__ == "__main__":
    main()
