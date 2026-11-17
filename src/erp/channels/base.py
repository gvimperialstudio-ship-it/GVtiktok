"""Channel client interface. Concrete HTTP clients are intentionally stubs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ListingStockCommand:
    """The PUT a channel client would send. Shadow mode logs this and stops."""

    method: str
    channel: str
    company_cnpj: str
    external_item_id: str
    external_variation_id: str | None
    action: str
    quantity: int

    def to_log_json(self) -> str:
        variation = self.external_variation_id or "default"
        body = {
            "method": self.method,
            "channel": self.channel,
            "path": f"/listings/{self.external_item_id}/variations/{variation}/stock",
            "body": {
                "company_cnpj": self.company_cnpj,
                "external_item_id": self.external_item_id,
                "external_variation_id": self.external_variation_id,
                "action": self.action,
                "quantity": self.quantity,
            },
        }
        return json.dumps(body, sort_keys=True, separators=(",", ":"))


class ChannelClient(Protocol):
    async def send_stock_command(self, command: ListingStockCommand) -> None:
        """Push a stock pause or quantity update. Not called while SHADOW_MODE is on."""
        ...
