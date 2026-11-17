"""Migration shape: required tables, the non-negative check, and Full isolation."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from erp.db.models import Warehouse
from erp.domain.enums import WarehouseType


async def test_migration_creates_core_tables(engine):
    def table_names(sync_connection) -> set[str]:
        from sqlalchemy import inspect

        return set(inspect(sync_connection).get_table_names())

    async with engine.connect() as connection:
        names = await connection.run_sync(table_names)
    assert {
        "companies",
        "company_channel_secrets",
        "warehouses",
        "skus",
        "kit_components",
        "stock_balances",
        "listing_links",
        "processed_events",
        "orders",
        "order_items",
        "invoices",
        "dlq_events",
        "ops_audit_log",
    } <= names


async def test_qty_available_cannot_go_negative(session_factory, seed):
    stock = await seed.simple_stock(qty=1)
    with pytest.raises(DBAPIError):
        async with session_factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "UPDATE stock_balances SET qty_available = -1 "
                        "WHERE warehouse_id = :warehouse AND sku_id = :sku"
                    ),
                    {"warehouse": stock.shared_warehouse_id, "sku": stock.sku_id},
                )


async def test_shared_view_excludes_full_balances(session_factory, seed):
    stock = await seed.simple_stock(qty=5, full_qty=100)
    async with session_factory() as session:
        shared_total = await session.scalar(
            text(
                "SELECT COALESCE(SUM(qty_available), 0) FROM shared_stock_balances "
                "WHERE sku_id = :sku"
            ),
            {"sku": stock.sku_id},
        )
        all_rows = await session.scalar(
            text("SELECT COUNT(*) FROM stock_balances WHERE sku_id = :sku"),
            {"sku": stock.sku_id},
        )
    assert int(shared_total) == 5
    assert int(all_rows) == 2


async def test_full_warehouse_code_must_match_cnpj(session_factory, seed):
    stock = await seed.simple_stock(qty=1)
    with pytest.raises(DBAPIError):
        async with session_factory() as session:
            async with session.begin():
                session.add(
                    Warehouse(
                        code="full_ml_99999999000199",
                        name="Wrong Full",
                        warehouse_type=WarehouseType.FULL_ML.value,
                        company_id=stock.company_id,
                    )
                )
                await session.flush()
