"""Channel client registry. HTTP implementations are stubs."""

from erp.channels.base import ChannelClient
from erp.channels.mercado_livre import MercadoLivreClient
from erp.channels.own_store import OwnStoreClient
from erp.channels.shopee import ShopeeClient
from erp.channels.stub_client import StubChannelClient


def stub_clients() -> dict[str, ChannelClient]:
    clients: list[StubChannelClient] = [
        MercadoLivreClient(),
        ShopeeClient(),
        OwnStoreClient(),
    ]
    return {client.channel: client for client in clients}
