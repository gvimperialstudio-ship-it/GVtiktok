"""Persist one ops audit row. Callers commit the surrounding transaction."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from erp.db.models import OpsAuditLog

OPS_ACTOR = "ops"


def add_audit(
    session: AsyncSession,
    *,
    action: str,
    request_path: str,
    result: str,
    request_body: dict[str, Any] | None = None,
) -> None:
    session.add(
        OpsAuditLog(
            id=uuid.uuid4(),
            action=action,
            actor=OPS_ACTOR,
            request_path=request_path,
            request_body=request_body,
            result=result,
        )
    )
