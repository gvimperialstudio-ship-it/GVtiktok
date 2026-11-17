"""Shared fail-closed channel stub. Concrete channels subclass this."""

from erp.channels.base import ListingStockCommand


class StubChannelClient:
    def __init__(self, channel: str) -> None:
        self.channel = channel

    async def send_stock_command(self, command: ListingStockCommand) -> None:
        raise NotImplementedError(
            f"Real {self.channel} HTTP client is not implemented. "
            "Keep SHADOW_MODE=true until that client exists. "
            f"Refused command for item {command.external_item_id}."
        )
