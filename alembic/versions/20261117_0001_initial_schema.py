"""Initial schema.

Stock, idempotency, listings, a structural invoice placeholder, DLQ, and the
ops audit log. No tax rules. Secret columns are references only.

Full-ML warehouse rows are isolated from shared stock by a check, a trigger,
and the shared_stock_balances view.

Revision ID: 20261117_0001
Revises:
Create Date: 2026-11-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261117_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CHANNEL_CHECK = "channel IN ('mercado_livre', 'shopee', 'own_store')"


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("cnpj", sa.String(length=14), nullable=False),
        sa.Column("legal_name", sa.String(length=255), nullable=False),
        sa.Column("origin_uf", sa.String(length=2), nullable=False),
        sa.Column(
            "a1_certificate_secret_ref",
            sa.String(length=512),
            nullable=True,
            comment="Secret reference only (OCID or name). Never the certificate bytes.",
        ),
        sa.Column(
            "cert_password_secret_ref",
            sa.String(length=512),
            nullable=True,
            comment="Secret reference only. Never the certificate password.",
        ),
        sa.Column(
            "rate_limit_config",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("cnpj ~ '^[0-9]{14}$'", name="ck_companies_cnpj_digits"),
        sa.CheckConstraint("origin_uf ~ '^[A-Z]{2}$'", name="ck_companies_origin_uf"),
        sa.UniqueConstraint("cnpj", name="uq_companies_cnpj"),
    )

    op.create_table(
        "company_channel_secrets",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column(
            "client_secret_ref",
            sa.String(length=512),
            nullable=False,
            comment="OAuth client_secret reference. Never the secret value.",
        ),
        sa.Column(
            "refresh_token_ref",
            sa.String(length=512),
            nullable=False,
            comment="OAuth refresh_token reference. Never the token value.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.CheckConstraint(CHANNEL_CHECK, name="ck_company_channel_secrets_channel"),
        sa.CheckConstraint(
            "length(client_secret_ref) > 0 AND length(refresh_token_ref) > 0",
            name="ck_company_channel_secrets_refs_present",
        ),
        sa.UniqueConstraint("company_id", "channel", name="uq_company_channel_secrets"),
    )

    op.create_table(
        "warehouses",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("warehouse_type", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "warehouse_type IN ('shared', 'full_ml')", name="ck_warehouses_type"
        ),
        sa.CheckConstraint(
            "(warehouse_type = 'shared' AND company_id IS NULL AND code = 'galpao_central') OR "
            "(warehouse_type = 'full_ml' AND company_id IS NOT NULL "
            "AND starts_with(code, 'full_ml_'))",
            name="ck_warehouses_isolation",
        ),
        sa.UniqueConstraint("code", name="uq_warehouses_code"),
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION warehouses_full_code_matches_cnpj()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            company_cnpj text;
        BEGIN
            IF NEW.warehouse_type = 'full_ml' THEN
                SELECT cnpj INTO company_cnpj FROM companies WHERE id = NEW.company_id;
                IF company_cnpj IS NULL OR NEW.code <> 'full_ml_' || company_cnpj THEN
                    RAISE EXCEPTION 'full warehouse code must be full_ml_<cnpj>';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_warehouses_full_code
        BEFORE INSERT OR UPDATE ON warehouses
        FOR EACH ROW
        EXECUTE FUNCTION warehouses_full_code_matches_cnpj();
        """
    )

    op.create_table(
        "skus",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("sku_code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("is_kit", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "safety_threshold", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "safety_threshold >= 0", name="ck_skus_safety_threshold_nonneg"
        ),
        sa.CheckConstraint("length(sku_code) > 0", name="ck_skus_code_present"),
        sa.UniqueConstraint("sku_code", name="uq_skus_sku_code"),
    )

    op.create_table(
        "kit_components",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("kit_sku_id", sa.Uuid(), nullable=False),
        sa.Column("component_sku_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["kit_sku_id"], ["skus.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["component_sku_id"], ["skus.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("quantity > 0", name="ck_kit_components_quantity_positive"),
        sa.CheckConstraint("kit_sku_id <> component_sku_id", name="ck_kit_components_not_self"),
        sa.UniqueConstraint(
            "kit_sku_id", "component_sku_id", name="uq_kit_components_pair"
        ),
    )

    op.create_table(
        "stock_balances",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("warehouse_id", sa.Uuid(), nullable=False),
        sa.Column("sku_id", sa.Uuid(), nullable=False),
        sa.Column("qty_on_hand", sa.Integer(), nullable=False),
        sa.Column(
            "qty_available",
            sa.Integer(),
            nullable=False,
            comment="Sellable units in THIS warehouse. Never sum Full into shared.",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["sku_id"], ["skus.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "qty_available >= 0", name="ck_stock_balances_qty_available_nonneg"
        ),
        sa.CheckConstraint("qty_on_hand >= 0", name="ck_stock_balances_qty_on_hand_nonneg"),
        sa.CheckConstraint(
            "qty_available <= qty_on_hand", name="ck_stock_balances_available_le_on_hand"
        ),
        sa.UniqueConstraint(
            "warehouse_id", "sku_id", name="uq_stock_balances_warehouse_sku"
        ),
    )
    op.execute(
        """
        CREATE VIEW shared_stock_balances AS
        SELECT sb.id,
               sb.warehouse_id,
               sb.sku_id,
               sb.qty_on_hand,
               sb.qty_available,
               sb.updated_at
        FROM stock_balances sb
        JOIN warehouses w ON w.id = sb.warehouse_id
        WHERE w.warehouse_type = 'shared';
        """
    )
    op.execute(
        """
        COMMENT ON VIEW shared_stock_balances IS
        'Shared galpao_central balances only. FULL_ML rows are excluded and must never be summed into shared stock.';
        """
    )

    op.create_table(
        "listing_links",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("sku_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("external_item_id", sa.String(length=128), nullable=False),
        sa.Column("external_variation_id", sa.String(length=128), nullable=True),
        sa.Column("is_full", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "sync_status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("last_synced_qty", sa.Integer(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["sku_id"], ["skus.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(CHANNEL_CHECK, name="ck_listing_links_channel"),
        sa.CheckConstraint(
            "sync_status IN ('pending', 'synced', 'shadow_logged', 'paused', 'error')",
            name="ck_listing_links_sync_status",
        ),
        sa.CheckConstraint("length(external_item_id) > 0", name="ck_listing_links_item_present"),
    )
    op.create_index("ix_listing_links_sku_id", "listing_links", ["sku_id"])
    op.create_index(
        "ix_listing_links_company_channel", "listing_links", ["company_id", "channel"]
    )
    op.create_index(
        "ix_listing_links_sku_active_nonfull",
        "listing_links",
        ["sku_id"],
        postgresql_where=sa.text("is_active AND NOT is_full"),
    )
    op.create_index(
        "uq_listing_links_one_primary",
        "listing_links",
        ["sku_id", "company_id", "channel"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_listing_links_external
        ON listing_links (
            channel,
            company_id,
            external_item_id,
            COALESCE(external_variation_id, '')
        );
        """
    )

    op.create_table(
        "processed_events",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("external_event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(CHANNEL_CHECK, name="ck_processed_events_channel"),
        sa.CheckConstraint(
            "length(external_event_id) > 0", name="ck_processed_events_id_present"
        ),
        sa.UniqueConstraint(
            "channel", "external_event_id", name="uq_processed_events_channel_event"
        ),
    )

    op.create_table(
        "orders",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column(
            "company_id",
            sa.Uuid(),
            nullable=False,
            comment="Store CNPJ holder. companies.cnpj is unique.",
        ),
        sa.Column("external_order_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("warehouse_id", sa.Uuid(), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(CHANNEL_CHECK, name="ck_orders_channel"),
        sa.CheckConstraint(
            "length(external_order_id) > 0", name="ck_orders_external_id_present"
        ),
        sa.UniqueConstraint(
            "channel",
            "company_id",
            "external_order_id",
            name="uq_orders_channel_company_external",
        ),
    )

    op.create_table(
        "order_items",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("sku_id", sa.Uuid(), nullable=False),
        sa.Column("warehouse_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sku_id"], ["skus.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("quantity > 0", name="ck_order_items_quantity_positive"),
        sa.UniqueConstraint("order_id", "sku_id", name="uq_order_items_order_sku"),
    )

    # TODO(accountant): structural placeholder only. No CFOP / CST / CSOSN / tax amounts.
    op.create_table(
        "invoices",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("invoice_role", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'PENDING'"),
        ),
        sa.Column("series", sa.String(length=8), nullable=True),
        sa.Column("invoice_number", sa.String(length=16), nullable=True),
        sa.Column("access_key", sa.String(length=44), nullable=True),
        sa.Column("protocol", sa.String(length=64), nullable=True),
        sa.Column(
            "xml_object_ref",
            sa.String(length=512),
            nullable=True,
            comment="Object-storage reference for the XML. Not the XML document.",
        ),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "invoice_role IN ('B2B_INPUT', 'CUSTOMER_NFE')", name="ck_invoices_role"
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'B2B_INPUT_ISSUED', 'CUSTOMER_NFE_ISSUED', "
            "'REJECTED', 'CANCELLED')",
            name="ck_invoices_status",
        ),
        sa.UniqueConstraint("order_id", "invoice_role", name="uq_invoices_order_role"),
    )

    op.create_table(
        "dlq_events",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("external_event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(CHANNEL_CHECK, name="ck_dlq_events_channel"),
        sa.CheckConstraint("status IN ('pending', 'resolved')", name="ck_dlq_events_status"),
        sa.CheckConstraint("attempt_count >= 1", name="ck_dlq_events_attempt_count"),
        sa.UniqueConstraint(
            "channel", "external_event_id", name="uq_dlq_events_channel_event"
        ),
    )
    op.create_index(
        "ix_dlq_events_status_created", "dlq_events", ["status", "created_at"]
    )

    op.create_table(
        "ops_audit_log",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("request_path", sa.String(length=512), nullable=False),
        sa.Column("request_body", postgresql.JSONB(), nullable=True),
        sa.Column("result", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_ops_audit_log_created_at", "ops_audit_log", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_ops_audit_log_created_at", table_name="ops_audit_log")
    op.drop_table("ops_audit_log")
    op.drop_index("ix_dlq_events_status_created", table_name="dlq_events")
    op.drop_table("dlq_events")
    op.drop_table("invoices")
    op.drop_table("order_items")
    op.drop_table("orders")
    op.drop_table("processed_events")
    op.execute("DROP INDEX IF EXISTS uq_listing_links_external")
    op.drop_index("uq_listing_links_one_primary", table_name="listing_links")
    op.drop_index("ix_listing_links_sku_active_nonfull", table_name="listing_links")
    op.drop_index("ix_listing_links_company_channel", table_name="listing_links")
    op.drop_index("ix_listing_links_sku_id", table_name="listing_links")
    op.drop_table("listing_links")
    op.execute("DROP VIEW IF EXISTS shared_stock_balances")
    op.drop_table("stock_balances")
    op.drop_table("kit_components")
    op.drop_table("skus")
    op.execute("DROP TRIGGER IF EXISTS trg_warehouses_full_code ON warehouses")
    op.execute("DROP FUNCTION IF EXISTS warehouses_full_code_matches_cnpj()")
    op.drop_table("warehouses")
    op.drop_table("company_channel_secrets")
    op.drop_table("companies")
