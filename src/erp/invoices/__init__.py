"""Invoice package. Status transitions only; no tax math."""

from erp.invoices.state_machine import transition

__all__ = ["transition"]
