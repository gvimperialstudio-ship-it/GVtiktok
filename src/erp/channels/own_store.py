"""Own-store client stub. HTTP stays unimplemented."""

from erp.channels.stub_client import StubChannelClient


class OwnStoreClient(StubChannelClient):
    def __init__(self) -> None:
        super().__init__("own_store")
