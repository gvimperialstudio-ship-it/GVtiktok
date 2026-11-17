"""FastAPI application.

The process does not call an LLM. /ops is the only HTTP surface in this PR;
marketplace order intake is invoked by workers and tests through StockService.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from erp import __version__
from erp.config import Settings, get_settings
from erp.db.session import create_engine, create_session_factory
from erp.ops.routes import router
from erp.ops.webhook import WebhookNotifier
from erp.queue.rate_limit import RedisRateLimiter
from erp.queue.redis_queue import RedisTaskQueue
from erp.secrets import build_secrets_provider


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    configure_logging(settings.log_level)
    secrets = build_secrets_provider(settings.secrets_provider)
    app.state.secrets = secrets
    engine = create_engine(settings.database_url)
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    app.state.redis = redis
    app.state.queue = RedisTaskQueue(redis)
    app.state.rate_limiter = RedisRateLimiter(redis)
    hmac_secret = secrets.get_secret(settings.webhook_hmac_secret_ref)
    app.state.webhook = WebhookNotifier(secret=hmac_secret, url=settings.webhook_url)
    try:
        yield
    finally:
        await redis.aclose()
        await engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(
        title="Auto Parts ERP",
        version=__version__,
        summary="Deterministic stock and operations API. No LLM in the stock or fiscal path.",
        lifespan=lifespan,
    )
    app.state.settings = settings or get_settings()
    app.include_router(router)
    return app
