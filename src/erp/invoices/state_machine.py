"""Invoice status machine.

TODO(accountant): this module deliberately does not know CFOP, CST, CSOSN,
ICMS, PIS, COFINS, or DIFAL. It only allows a status to move. Tax calculation
and NF-e XML generation stay unimplemented until the accountant approves the
rules for each origin UF and tax regime.
"""

from erp.domain.enums import InvoiceStatus
from erp.domain.errors import InvalidInvoiceTransition

ALLOWED: dict[str, frozenset[str]] = {
    InvoiceStatus.PENDING.value: frozenset(
        {
            InvoiceStatus.B2B_INPUT_ISSUED.value,
            InvoiceStatus.CUSTOMER_NFE_ISSUED.value,
            InvoiceStatus.REJECTED.value,
            InvoiceStatus.CANCELLED.value,
        }
    ),
    InvoiceStatus.B2B_INPUT_ISSUED.value: frozenset(
        {
            InvoiceStatus.CUSTOMER_NFE_ISSUED.value,
            InvoiceStatus.REJECTED.value,
            InvoiceStatus.CANCELLED.value,
        }
    ),
    InvoiceStatus.CUSTOMER_NFE_ISSUED.value: frozenset({InvoiceStatus.CANCELLED.value}),
    InvoiceStatus.REJECTED.value: frozenset({InvoiceStatus.PENDING.value}),
    InvoiceStatus.CANCELLED.value: frozenset(),
}


def transition(current: str, new: str) -> str:
    """Return ``new`` when the move is legal. Raise otherwise."""

    allowed = ALLOWED.get(current)
    if allowed is None or new not in allowed:
        raise InvalidInvoiceTransition(current, new)
    return new
