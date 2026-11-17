"""Mercado Livre client stub. HTTP stays unimplemented."""

from erp.channels.stub_client import StubChannelClient


class MercadoLivreClient(StubChannelClient):
    def __init__(self) -> None:
        super().__init__("mercado_livre")
