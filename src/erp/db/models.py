"""PostgreSQL schema.

Stock lives only in ``stock_balances``. Redis is not referenced here.
Secret columns store references (OCID or name), never secret values.

TODO(accountant): ``invoices`` is a structural placeholder plus a status
machine. Do not add CFOP, CST, CSOSN, or tax amount columns until the
accountant approves the matrix. See ``erp.invoices.state_machine``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from erp.db.base import Base
from erp.domain.enums import (
    CHANNEL_VALUES,
    DLQ_STATUS_VALUES,
    INVOICE_ROLE_VALUES,
    INVOICE_STATUS_VALUES,
    SYNC_STATUS_VALUES,
    WAREHOUSE_TYPE_VALUES,
)


def _in(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


class Company(Base):
    """A selling or partner CNPJ.

    Certificate material is a Vault reference. The certificate bytes and the
    password are never columns on this table.
    """

    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cnpj: Mapped[str] = mapped_column(String(14), nullable=False, unique=True)
    legal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    origin_uf: Mapped[str] = mapped_column(String(2), nullable=False)
    a1_certificate_secret_ref: Mapped[str | None] = mapped_column(String(512))
    cert_password_secret_ref: Mapped[str | None] = mapped_column(String(512))
    rate_limit_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("cnpj ~ '^[0-9]{14}$'", name="ck_companies_cnpj_digits"),
        CheckConstraint("origin_uf ~ '^[A-Z]{2}$'", name="ck_companies_origin_uf"),
    )


class CompanyChannelSecret(Base):
    """Per-channel OAuth secret references. ``client_secret_ref`` is not the secret."""

    __tablename__ = "company_channel_secrets"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    client_secret_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    refresh_token_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("company_id", "channel", name="uq_company_channel_secrets"),
        CheckConstraint(_in("channel", CHANNEL_VALUES), name="ck_company_channel_secrets_channel"),
        CheckConstraint(
            "length(client_secret_ref) > 0 AND length(refresh_token_ref) > 0",
            name="ck_company_channel_secrets_refs_present",
        ),
    )


class Warehouse(Base):
    """``galpao_central`` is the single shared warehouse.

    ``full_ml_<cnpj>`` is isolated per company. Full balances must never be
    added to shared balances. A trigger pins the Full code to that CNPJ.
    """

    __tablename__ = "warehouses"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("companies.id", ondelete="RESTRICT"), nullable=True
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    warehouse_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(_in("warehouse_type", WAREHOUSE_TYPE_VALUES), name="ck_warehouses_type"),
        CheckConstraint(
            "(warehouse_type = 'shared' AND company_id IS NULL AND code = 'galpao_central') OR "
            "(warehouse_type = 'full_ml' AND company_id IS NOT NULL "
            "AND starts_with(code, 'full_ml_'))",
            name="ck_warehouses_isolation",
        ),
    )


class Sku(Base):
    __tablename__ = "skus"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    sku_code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_kit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    safety_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("safety_threshold >= 0", name="ck_skus_safety_threshold_nonneg"),
        CheckConstraint("length(sku_code) > 0", name="ck_skus_code_present"),
    )


class KitComponent(Base):
    """One-level kit recipe. Components are physical SKUs, not nested kits."""

    __tablename__ = "kit_components"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    kit_sku_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("skus.id", ondelete="RESTRICT"), nullable=False
    )
    component_sku_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("skus.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("kit_sku_id", "component_sku_id", name="uq_kit_components_pair"),
        CheckConstraint("quantity > 0", name="ck_kit_components_quantity_positive"),
        CheckConstraint("kit_sku_id <> component_sku_id", name="ck_kit_components_not_self"),
    )


class StockBalance(Base):
    """Single source of truth for a SKU inside one warehouse.

    The conditional decrement refuses to take ``qty_available`` below zero.
    Do not cache this quantity in Redis.
    """

    __tablename__ = "stock_balances"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False
    )
    sku_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("skus.id", ondelete="RESTRICT"), nullable=False
    )
    qty_on_hand: Mapped[int] = mapped_column(Integer, nullable=False)
    qty_available: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("warehouse_id", "sku_id", name="uq_stock_balances_warehouse_sku"),
        CheckConstraint("qty_available >= 0", name="ck_stock_balances_qty_available_nonneg"),
        CheckConstraint("qty_on_hand >= 0", name="ck_stock_balances_qty_on_hand_nonneg"),
        CheckConstraint(
            "qty_available <= qty_on_hand", name="ck_stock_balances_available_le_on_hand"
        ),
    )


class ListingLink(Base):
    """Marketplace listing bound to one SKU and one CNPJ.

    ``is_full`` listings are fulfilled from that CNPJ's Full warehouse and are
    excluded from shared-stock fan-out. The external-id unique index treats a
    null variation as an empty string (created in the migration).
    """

    __tablename__ = "listing_links"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    sku_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("skus.id", ondelete="RESTRICT"), nullable=False
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    external_item_id: Mapped[str] = mapped_column(String(128), nullable=False)
    external_variation_id: Mapped[str | None] = mapped_column(String(128))
    is_full: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sync_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    last_synced_qty: Mapped[int | None] = mapped_column(Integer)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(_in("channel", CHANNEL_VALUES), name="ck_listing_links_channel"),
        CheckConstraint(_in("sync_status", SYNC_STATUS_VALUES), name="ck_listing_links_sync_status"),
        CheckConstraint("length(external_item_id) > 0", name="ck_listing_links_item_present"),
        Index("ix_listing_links_sku_id", "sku_id"),
        Index("ix_listing_links_company_channel", "company_id", "channel"),
        Index(
            "ix_listing_links_sku_active_nonfull",
            "sku_id",
            postgresql_where=text("is_active AND NOT is_full"),
        ),
        Index(
            "uq_listing_links_one_primary",
            "sku_id",
            "company_id",
            "channel",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
    )


class ProcessedEvent(Base):
    """Idempotency ledger. A duplicate (channel, external_event_id) is a no-op."""

    __tablename__ = "processed_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    external_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "channel", "external_event_id", name="uq_processed_events_channel_event"
        ),
        CheckConstraint(_in("channel", CHANNEL_VALUES), name="ck_processed_events_channel"),
        CheckConstraint("length(external_event_id) > 0", name="ck_processed_events_id_present"),
    )


class Order(Base):
    """Marketplace order for one store CNPJ.

    Uniqueness is (channel, company_id, external_order_id). ``company_id`` is
    the store: ``companies.cnpj`` is unique, so this is the store-CNPJ key.
    """

    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("companies.id", ondelete="RESTRICT"), nullable=False
    )
    external_order_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False
    )
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "channel",
            "company_id",
            "external_order_id",
            name="uq_orders_channel_company_external",
        ),
        CheckConstraint(_in("channel", CHANNEL_VALUES), name="ck_orders_channel"),
        CheckConstraint("length(external_order_id) > 0", name="ck_orders_external_id_present"),
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    sku_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("skus.id", ondelete="RESTRICT"), nullable=False
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("order_id", "sku_id", name="uq_order_items_order_sku"),
        CheckConstraint("quantity > 0", name="ck_order_items_quantity_positive"),
    )


class Invoice(Base):
    """Structural NF-e placeholder and status machine.

    TODO(accountant): do not encode CFOP, CST, CSOSN, ICMS, PIS, COFINS, or
    DIFAL here. Those rules wait for an approved tax matrix.
    """

    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False
    )
    invoice_role: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    series: Mapped[str | None] = mapped_column(String(8))
    invoice_number: Mapped[str | None] = mapped_column(String(16))
    access_key: Mapped[str | None] = mapped_column(String(44))
    protocol: Mapped[str | None] = mapped_column(String(64))
    xml_object_ref: Mapped[str | None] = mapped_column(String(512))
    status_reason: Mapped[str | None] = mapped_column(Text)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("order_id", "invoice_role", name="uq_invoices_order_role"),
        CheckConstraint(_in("invoice_role", INVOICE_ROLE_VALUES), name="ck_invoices_role"),
        CheckConstraint(_in("status", INVOICE_STATUS_VALUES), name="ck_invoices_status"),
    )


class DlqEvent(Base):
    """Dead-letter row. Retry is safe because stock applies ``processed_events``."""

    __tablename__ = "dlq_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    external_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("channel", "external_event_id", name="uq_dlq_events_channel_event"),
        CheckConstraint(_in("channel", CHANNEL_VALUES), name="ck_dlq_events_channel"),
        CheckConstraint(_in("status", DLQ_STATUS_VALUES), name="ck_dlq_events_status"),
        CheckConstraint("attempt_count >= 1", name="ck_dlq_events_attempt_count"),
        Index("ix_dlq_events_status_created", "status", "created_at"),
    )


class OpsAuditLog(Base):
    """One row per /ops write. The bearer token itself is never stored."""

    __tablename__ = "ops_audit_log"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    request_path: Mapped[str] = mapped_column(String(512), nullable=False)
    request_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_ops_audit_log_created_at", "created_at"),)
