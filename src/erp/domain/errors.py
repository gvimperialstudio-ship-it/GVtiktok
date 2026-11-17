"""Domain errors. None of these carry secret values."""


class ErpError(Exception):
    """Base class for expected ERP failures."""


class SecretNotFound(ErpError):
    def __init__(self, ref: str) -> None:
        self.ref = ref
        super().__init__(f"Secret reference could not be resolved: {ref}")


class SecretRefNotSupported(ErpError):
    pass


class InsufficientStock(ErpError):
    def __init__(self, sku_id: object, warehouse_id: object, quantity: int) -> None:
        self.sku_id = sku_id
        self.warehouse_id = warehouse_id
        self.quantity = quantity
        super().__init__(
            f"Insufficient stock for sku {sku_id} in warehouse {warehouse_id}: "
            f"requested {quantity}"
        )


class SkuNotFound(ErpError):
    def __init__(self, sku_id: object) -> None:
        self.sku_id = sku_id
        super().__init__(f"SKU not found: {sku_id}")


class KitHasNoComponents(ErpError):
    def __init__(self, sku_id: object) -> None:
        self.sku_id = sku_id
        super().__init__(f"Kit SKU {sku_id} has no components")


class NestedKitNotSupported(ErpError):
    def __init__(self, sku_id: object) -> None:
        self.sku_id = sku_id
        super().__init__(
            f"Kit SKU {sku_id} contains another kit. Only one-level kits are supported."
        )


class InvalidInvoiceTransition(ErpError):
    def __init__(self, current: str, new: str) -> None:
        self.current = current
        self.new = new
        super().__init__(f"Invoice status cannot move from {current} to {new}")


class UnknownDlqEventType(ErpError):
    def __init__(self, event_type: str) -> None:
        self.event_type = event_type
        super().__init__(f"DLQ event type is not retryable: {event_type}")
