"""Deterministic multi-CNPJ ERP for marketplace auto-parts stock.

Stock quantities and invoice status transitions are plain code. This package
does not call an LLM to decide stock, tax, or NF-e content. An external
supervisor may only read /ops/* and ask for idempotent retries.
"""

__version__ = "0.1.0"
