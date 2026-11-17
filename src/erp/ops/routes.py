"""Authenticated /ops endpoints for the external supervisor.

Reads: health and stock audit. The only write is an idempotent DLQ retry,
which is appended to ops_audit_log. The bearer token is not the channel OAuth
token; it is a separate secret resolved by the secrets provider.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.db.models import DlqEvent
from erp.ops.auth import require_ops_bearer
from erp.ops.retry import retry_dlq_event
from erp.ops.stock_audit import build_stock_audit

logger = logging.getLogger("erp.ops")

router = APIRouter(
    prefix="/ops",
    dependencies=[Depends(require_ops_bearer)],
    tags=["ops"],
)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory = request.app.state.session_factory
    async with factory() as session:
        yield session


@router.get("/health")
async def health(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> JSONResponse:
    postgres = "ok"
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        logger.exception("postgres health check failed")
        postgres = "error"
    redis_status = "ok"
    try:
        pong = await request.app.state.redis.ping()
        if not pong:
            redis_status = "error"
    except Exception:
        logger.exception("redis health check failed")
        redis_status = "error"
    status = "ok" if postgres == "ok" and redis_status == "ok" else "degraded"
    payload = {
        "status": status,
        "postgres": postgres,
        "redis": redis_status,
        "shadow_mode": bool(request.app.state.settings.shadow_mode),
    }
    return JSONResponse(status_code=200 if postgres == "ok" else 503, content=payload)


@router.get("/stock-audit")
async def stock_audit(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    return {"items": await build_stock_audit(session)}


@router.get("/dlq")
async def list_dlq(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    rows = (
        await session.scalars(select(DlqEvent).order_by(DlqEvent.created_at.desc(), DlqEvent.id))
    ).all()
    return {
        "items": [
            {
                "id": str(row.id),
                "channel": row.channel,
                "external_event_id": row.external_event_id,
                "event_type": row.event_type,
                "payload": row.payload,
                "error_message": row.error_message,
                "attempt_count": row.attempt_count,
                "status": row.status,
                "created_at": row.created_at.isoformat(),
                "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
            }
            for row in rows
        ]
    }


@router.post("/dlq/{event_id}/retry")
async def retry_dlq(
    event_id: uuid.UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> JSONResponse:
    outcome = await retry_dlq_event(
        session, event_id, request_path=f"/ops/dlq/{event_id}/retry"
    )
    if outcome.tasks:
        await request.app.state.queue.enqueue_many(outcome.tasks)
    if outcome.notify_failure:
        await _notify_failure(request, event_id)
    return JSONResponse(status_code=outcome.status_code, content=outcome.body)


async def _notify_failure(request: Request, event_id: uuid.UUID) -> None:
    webhook = request.app.state.webhook
    try:
        await webhook.send("dlq.retry_failed", {"dlq_event_id": str(event_id)})
    except Exception:
        logger.exception("webhook notifier failed for dlq %s", event_id)
