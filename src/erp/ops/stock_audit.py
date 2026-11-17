"""Read-only comparison of PostgreSQL balances and listing_links.last_synced_qty.

Non-Full listings are compared to shared sellable stock after the safety
threshold. Full listings are compared only to that CNPJ's Full warehouse.
The two quantities are never added together.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from erp.db.models import Company, ListingLink, Sku
from erp.domain.enums import SHARED_WAREHOUSE_CODE
from erp.stock.availability import full_qty_available, sellable_qty
from erp.stock.policy import decide_listing_action


async def build_stock_audit(session: AsyncSession) -> list[dict]:
    skus = {sku.id: sku for sku in (await session.scalars(select(Sku))).all()}
    companies = {
        company.id: company for company in (await session.scalars(select(Company))).all()
    }
    links = (
        await session.scalars(select(ListingLink).order_by(ListingLink.created_at, ListingLink.id))
    ).all()
    shared_cache: dict = {}
    items: list[dict] = []
    for link in links:
        sku = skus[link.sku_id]
        company = companies[link.company_id]
        if link.is_full:
            code, qty = await full_qty_available(
                session, company_id=company.id, sku_id=sku.id
            )
            items.append(
                _item(
                    link=link,
                    sku=sku,
                    company=company,
                    source_warehouse_code=code,
                    source_qty=qty,
                    decision="not_synced_by_shared_worker",
                    expected=qty,
                    full_isolated=True,
                )
            )
            continue
        if sku.id not in shared_cache:
            shared_cache[sku.id] = await sellable_qty(session, sku.id, is_kit=sku.is_kit)
        sellable = shared_cache[sku.id]
        action, expected = decide_listing_action(
            sellable_qty=sellable, safety_threshold=sku.safety_threshold
        )
        items.append(
            _item(
                link=link,
                sku=sku,
                company=company,
                source_warehouse_code=SHARED_WAREHOUSE_CODE,
                source_qty=sellable,
                decision=action,
                expected=expected,
                full_isolated=False,
            )
        )
    return items


def _item(
    *,
    link: ListingLink,
    sku: Sku,
    company: Company,
    source_warehouse_code: str | None,
    source_qty: int | None,
    decision: str,
    expected: int | None,
    full_isolated: bool,
) -> dict:
    return {
        "listing_link_id": str(link.id),
        "sku_code": sku.sku_code,
        "channel": link.channel,
        "external_item_id": link.external_item_id,
        "external_variation_id": link.external_variation_id,
        "company_cnpj": company.cnpj,
        "is_full": link.is_full,
        "is_active": link.is_active,
        "source_warehouse_code": source_warehouse_code,
        "source_qty_available": source_qty,
        "safety_threshold": sku.safety_threshold,
        "decision": decision,
        "expected_remote_qty": expected,
        "last_synced_qty": link.last_synced_qty,
        "sync_status": link.sync_status,
        "in_sync": expected is not None and link.last_synced_qty == expected,
        "full_isolated": full_isolated,
    }
