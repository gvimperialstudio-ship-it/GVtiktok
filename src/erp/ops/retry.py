"""Idempotent DLQ retry. Stock changes go through StockService in one transaction."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from erp.db.models import DlqEvent
from erp.domain.enums import EVENT_STOCK_DECREMENT, EVENT_STOCK_SYNC, DlqStatus
from erp.domain.errors import InsufficientStock, UnknownDlqEventType
from erp.ops.audit import add_audit
from erp.stock.availability import StockSyncTask
from erp.stock.service import StockService, stock_event_from_payload


@dataclass
class RetryOutcome:
    status_code: int
    body: dict[str, Any]
    tasks: tuple[StockSyncTask, ...] = ()
    notify_failure: bool = False


async def retry_dlq_event(
    session: AsyncSession, event_id: uuid.UUID, *, request_path: str
) -> RetryOutcome:
    body_ref = {"dlq_event_id": str(event_id)}
    try:
        async with session.begin():
            dlq = await session.get(DlqEvent, event_id, with_for_update=True)
            if dlq is None:
                add_audit(
                    session,
                    action="dlq.retry",
                    request_path=request_path,
                    request_body=body_ref,
                    result="not_found",
                )
                return RetryOutcome(status_code=404, body={"detail": "Not found"})
            if dlq.status == DlqStatus.RESOLVED.value:
                add_audit(
                    session,
                    action="dlq.retry",
                    request_path=request_path,
                    request_body=body_ref,
                    result="already_resolved",
                )
                return RetryOutcome(
                    status_code=200,
                    body=_body(event_id, "already_resolved"),
                )
            outcome, tasks = await _apply(session, dlq)
            now = datetime.now(timezone.utc)
            dlq.attempt_count += 1
            dlq.last_attempt_at = now
            dlq.status = DlqStatus.RESOLVED.value
            dlq.resolved_at = now
            add_audit(
                session,
                action="dlq.retry",
                request_path=request_path,
                request_body=body_ref,
                result=outcome,
            )
            return RetryOutcome(
                status_code=200,
                body=_body(event_id, outcome),
                tasks=tasks,
            )
    except InsufficientStock:
        await _record_failure(session, event_id, request_path, body_ref, "insufficient_stock")
        return RetryOutcome(
            status_code=409,
            body={"detail": "Insufficient stock", "dlq_event_id": str(event_id)},
            notify_failure=True,
        )
    except UnknownDlqEventType:
        await _record_failure(session, event_id, request_path, body_ref, "unsupported")
        return RetryOutcome(
            status_code=422,
            body={"detail": "Unsupported DLQ event", "dlq_event_id": str(event_id)},
        )


async def _apply(
    session: AsyncSession, dlq: DlqEvent
) -> tuple[str, tuple[StockSyncTask, ...]]:
    if dlq.event_type == EVENT_STOCK_DECREMENT:
        result = await StockService(session).apply_event(stock_event_from_payload(dlq.payload))
        return result.status, result.fanout_tasks
    if dlq.event_type == EVENT_STOCK_SYNC:
        payload = dlq.payload
        task = StockSyncTask(
            listing_link_id=uuid.UUID(str(payload["listing_link_id"])),
            sku_id=uuid.UUID(str(payload["sku_id"])),
        )
        return "requeued", (task,)
    raise UnknownDlqEventType(dlq.event_type)


async def _record_failure(
    session: AsyncSession,
    event_id: uuid.UUID,
    request_path: str,
    body_ref: dict[str, str],
    result: str,
) -> None:
    async with session.begin():
        dlq = await session.get(DlqEvent, event_id)
        if dlq is not None and dlq.status != DlqStatus.RESOLVED.value:
            dlq.attempt_count += 1
            dlq.last_attempt_at = datetime.now(timezone.utc)
            dlq.status = DlqStatus.PENDING.value
            dlq.error_message = result
        add_audit(
            session,
            action="dlq.retry",
            request_path=request_path,
            request_body=body_ref,
            result=result,
        )


def _body(event_id: uuid.UUID, status: str) -> dict[str, Any]:
    return {
        "dlq_event_id": str(event_id),
        "status": status,
        "idempotent": status in {"duplicate", "already_resolved"},
    }
