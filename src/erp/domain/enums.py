"""Shared enumerations. Values are stored as strings, not native PG enums."""

from enum import StrEnum


class Channel(StrEnum):
    MERCADO_LIVRE = "mercado_livre"
    SHOPEE = "shopee"
    OWN_STORE = "own_store"


class WarehouseType(StrEnum):
    SHARED = "shared"
    FULL_ML = "full_ml"


class SyncStatus(StrEnum):
    PENDING = "pending"
    SYNCED = "synced"
    SHADOW_LOGGED = "shadow_logged"
    PAUSED = "paused"
    ERROR = "error"


class InvoiceRole(StrEnum):
    B2B_INPUT = "B2B_INPUT"
    CUSTOMER_NFE = "CUSTOMER_NFE"


class InvoiceStatus(StrEnum):
    PENDING = "PENDING"
    B2B_INPUT_ISSUED = "B2B_INPUT_ISSUED"
    CUSTOMER_NFE_ISSUED = "CUSTOMER_NFE_ISSUED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class DlqStatus(StrEnum):
    PENDING = "pending"
    RESOLVED = "resolved"


class OrderStatus(StrEnum):
    STOCK_DECREMENTED = "stock_decremented"


CHANNEL_VALUES = tuple(item.value for item in Channel)
WAREHOUSE_TYPE_VALUES = tuple(item.value for item in WarehouseType)
SYNC_STATUS_VALUES = tuple(item.value for item in SyncStatus)
INVOICE_ROLE_VALUES = tuple(item.value for item in InvoiceRole)
INVOICE_STATUS_VALUES = tuple(item.value for item in InvoiceStatus)
DLQ_STATUS_VALUES = tuple(item.value for item in DlqStatus)

SHARED_WAREHOUSE_CODE = "galpao_central"
FULL_WAREHOUSE_PREFIX = "full_ml_"

EVENT_STOCK_DECREMENT = "order.stock_decrement"
EVENT_STOCK_SYNC = "stock_sync"

# Redis keys. These hold queue pointers and rate-limit counters, never stock.
STOCK_SYNC_QUEUE_KEY = "queue:stock_sync"
RATE_LIMIT_KEY_PREFIX = "ratelimit"
