"""Shared rows for integration tests. Not a stock authority of its own."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker

from erp.db.models import (
    Company,
    KitComponent,
    ListingLink,
    Sku,
    StockBalance,
    Warehouse,
)
from erp.domain.enums import FULL_WAREHOUSE_PREFIX, SHARED_WAREHOUSE_CODE, WarehouseType


@dataclass(frozen=True)
class SimpleStock:
    company_id: uuid.UUID
    cnpj: str
    shared_warehouse_id: uuid.UUID
    full_warehouse_id: uuid.UUID
    sku_id: uuid.UUID
    sku_code: str


@dataclass(frozen=True)
class KitStock:
    company_id: uuid.UUID
    cnpj: str
    shared_warehouse_id: uuid.UUID
    full_warehouse_id: uuid.UUID
    kit_sku_id: uuid.UUID
    component_a_id: uuid.UUID
    component_b_id: uuid.UUID


class Seed:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self.session_factory = session_factory

    async def simple_stock(
        self,
        *,
        qty: int,
        safety_threshold: int = 0,
        cnpj: str = "11111111000191",
        sku_code: str = "PASTILHA-01",
        full_qty: int = 0,
    ) -> SimpleStock:
        async with self.session_factory() as session:
            async with session.begin():
                company = Company(
                    cnpj=cnpj,
                    legal_name="Auto Pecas Teste LTDA",
                    origin_uf="SP",
                    a1_certificate_secret_ref="ocid1.vaultsecret.oc1.sa-saopaulo-1.examplecert",
                    cert_password_secret_ref="ocid1.vaultsecret.oc1.sa-saopaulo-1.examplepass",
                    rate_limit_config={"default_rps": 10, "mercado_livre": 8},
                )
                session.add(company)
                await session.flush()
                shared = Warehouse(
                    code=SHARED_WAREHOUSE_CODE,
                    name="Galpao Central",
                    warehouse_type=WarehouseType.SHARED.value,
                    company_id=None,
                )
                full = Warehouse(
                    code=f"{FULL_WAREHOUSE_PREFIX}{cnpj}",
                    name=f"Full ML {cnpj}",
                    warehouse_type=WarehouseType.FULL_ML.value,
                    company_id=company.id,
                )
                sku = Sku(
                    sku_code=sku_code,
                    name="Pastilha de freio",
                    is_kit=False,
                    safety_threshold=safety_threshold,
                )
                session.add_all([shared, full, sku])
                await session.flush()
                session.add(
                    StockBalance(
                        warehouse_id=shared.id,
                        sku_id=sku.id,
                        qty_on_hand=qty,
                        qty_available=qty,
                    )
                )
                if full_qty:
                    session.add(
                        StockBalance(
                            warehouse_id=full.id,
                            sku_id=sku.id,
                            qty_on_hand=full_qty,
                            qty_available=full_qty,
                        )
                    )
                return SimpleStock(
                    company_id=company.id,
                    cnpj=cnpj,
                    shared_warehouse_id=shared.id,
                    full_warehouse_id=full.id,
                    sku_id=sku.id,
                    sku_code=sku_code,
                )

    async def kit_stock(
        self,
        *,
        qty_a: int,
        qty_b: int,
        per_a: int = 2,
        per_b: int = 1,
        cnpj: str = "22222222000191",
    ) -> KitStock:
        async with self.session_factory() as session:
            async with session.begin():
                company = Company(
                    cnpj=cnpj,
                    legal_name="Parceiro Freios LTDA",
                    origin_uf="PR",
                    rate_limit_config={},
                )
                session.add(company)
                await session.flush()
                shared = Warehouse(
                    code=SHARED_WAREHOUSE_CODE,
                    name="Galpao Central",
                    warehouse_type=WarehouseType.SHARED.value,
                )
                full = Warehouse(
                    code=f"{FULL_WAREHOUSE_PREFIX}{cnpj}",
                    name=f"Full ML {cnpj}",
                    warehouse_type=WarehouseType.FULL_ML.value,
                    company_id=company.id,
                )
                component_a = Sku(sku_code="DISCO", name="Disco", is_kit=False, safety_threshold=0)
                component_b = Sku(
                    sku_code="PASTILHA", name="Pastilha", is_kit=False, safety_threshold=0
                )
                kit = Sku(
                    sku_code="KIT-FREIO",
                    name="Kit freio",
                    is_kit=True,
                    safety_threshold=0,
                )
                session.add_all([shared, full, component_a, component_b, kit])
                await session.flush()
                session.add_all(
                    [
                        KitComponent(
                            kit_sku_id=kit.id,
                            component_sku_id=component_a.id,
                            quantity=per_a,
                        ),
                        KitComponent(
                            kit_sku_id=kit.id,
                            component_sku_id=component_b.id,
                            quantity=per_b,
                        ),
                        StockBalance(
                            warehouse_id=shared.id,
                            sku_id=component_a.id,
                            qty_on_hand=qty_a,
                            qty_available=qty_a,
                        ),
                        StockBalance(
                            warehouse_id=shared.id,
                            sku_id=component_b.id,
                            qty_on_hand=qty_b,
                            qty_available=qty_b,
                        ),
                    ]
                )
                return KitStock(
                    company_id=company.id,
                    cnpj=cnpj,
                    shared_warehouse_id=shared.id,
                    full_warehouse_id=full.id,
                    kit_sku_id=kit.id,
                    component_a_id=component_a.id,
                    component_b_id=component_b.id,
                )

    async def add_listing(
        self,
        *,
        sku_id: uuid.UUID,
        company_id: uuid.UUID,
        channel: str,
        external_item_id: str,
        is_full: bool = False,
        is_active: bool = True,
        is_primary: bool = False,
        external_variation_id: str | None = None,
        last_synced_qty: int | None = None,
    ) -> uuid.UUID:
        async with self.session_factory() as session:
            async with session.begin():
                link = ListingLink(
                    sku_id=sku_id,
                    company_id=company_id,
                    channel=channel,
                    external_item_id=external_item_id,
                    external_variation_id=external_variation_id,
                    is_full=is_full,
                    is_active=is_active,
                    is_primary=is_primary,
                    last_synced_qty=last_synced_qty,
                )
                session.add(link)
                await session.flush()
                return link.id

