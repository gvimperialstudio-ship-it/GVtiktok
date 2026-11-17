"""Shopee client stub. HTTP stays unimplemented."""

from erp.channels.stub_client import StubChannelClient


class ShopeeClient(StubChannelClient):
    def __init__(self) -> None:
        super().__init__("shopee")
