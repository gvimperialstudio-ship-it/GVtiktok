"""HMAC-SHA256 webhook notifier.

The signing secret comes from the secrets provider. With an empty URL this is
a stub: it signs and logs, and it does not open a socket.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

import httpx

logger = logging.getLogger("erp.webhook")


def sign_body(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


class WebhookNotifier:
    def __init__(self, secret: str, url: str = "") -> None:
        self._secret = secret
        self._url = url

    def signature_for(self, event_type: str, payload: dict[str, Any]) -> tuple[bytes, str]:
        body = json.dumps(
            {"event_type": event_type, "payload": payload},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return body, sign_body(self._secret, body)

    async def send(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        body, signature = self.signature_for(event_type, payload)
        if not self._url:
            logger.info(
                "webhook notifier stub: event=%s signature=%s delivery=skipped",
                event_type,
                signature,
            )
            return {"delivered": False, "signature": signature}
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                self._url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-ERP-Signature": signature,
                },
            )
            response.raise_for_status()
        return {"delivered": True, "signature": signature}
