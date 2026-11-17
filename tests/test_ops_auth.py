"""/ops bearer auth, DLQ retry audit, and the read-only stock audit."""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from erp.config import Settings
from erp.db.models import DlqEvent, OpsAuditLog, StockBalance
from erp.domain.enums import Channel
from erp.main import create_app
from erp.stock.service import OrderLine, StockDecrementEvent


def _settings(database_url: str, redis_url: str) -> Settings:
    return Settings(
        database_url=database_url,
        redis_url=redis_url,
        shadow_mode=True,
        secrets_provider="local",
        ops_bearer_token_ref="env:OPS_BEARER_TOKEN",
        webhook_hmac_secret_ref="env:WEBHOOK_HMAC_SECRET",
        webhook_url="",
    )


@pytest.fixture
async def ops_client(database_url, redis_url, monkeypatch):
    monkeypatch.setenv("OPS_BEARER_TOKEN", "ops-test-token")
    monkeypatch.setenv("WEBHOOK_HMAC_SECRET", "hmac-test-secret")
    app = create_app(_settings(database_url, redis_url))
    # httpx's ASGI transport does not run the FastAPI lifespan. Enter it here
    # so the test process resolves secrets the same way production startup does.
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


def _auth(token: str = "ops-test-token") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_ops_requires_bearer_from_the_secrets_provider(ops_client, monkeypatch):
    missing = await ops_client.get("/ops/health")
    wrong = await ops_client.get("/ops/health", headers=_auth("wrong-token"))
    allowed = await ops_client.get("/ops/health", headers=_auth())
    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert allowed.status_code == 200
    body = allowed.json()
    assert body["postgres"] == "ok"
    assert body["redis"] == "ok"
    assert body["shadow_mode"] is True

    unauthenticated_write = await ops_client.post(f"/ops/dlq/{uuid.uuid4()}/retry")
    assert unauthenticated_write.status_code == 401

    monkeypatch.setenv("OPS_BEARER_TOKEN", "rotated-token")
    stale = await ops_client.get("/ops/health", headers=_auth("ops-test-token"))
    rotated = await ops_client.get("/ops/health", headers=_auth("rotated-token"))
    assert stale.status_code == 401
    assert rotated.status_code == 200


async def test_dlq_retry_is_idempotent_and_audited(ops_client, session_factory, seed):
    stock = await seed.simple_stock(qty=5)
    event = StockDecrementEvent(
        channel=Channel.MERCADO_LIVRE.value,
        external_event_id="evt-dlq",
        company_id=stock.company_id,
        external_order_id="ord-dlq",
        warehouse_id=stock.shared_warehouse_id,
        lines=(OrderLine(sku_id=stock.sku_id, quantity=1),),
    )
    async with session_factory() as session:
        async with session.begin():
            row = DlqEvent(
                channel=event.channel,
                external_event_id=event.external_event_id,
                event_type=event.event_type,
                payload=event.as_payload(),
                error_message="temporary downstream failure",
                attempt_count=1,
                status="pending",
            )
            session.add(row)
            await session.flush()
            dlq_id = row.id

    listed = await ops_client.get("/ops/dlq", headers=_auth())
    assert listed.status_code == 200
    assert listed.json()["items"][0]["id"] == str(dlq_id)

    first = await ops_client.post(f"/ops/dlq/{dlq_id}/retry", headers=_auth())
    second = await ops_client.post(f"/ops/dlq/{dlq_id}/retry", headers=_auth())
    assert first.status_code == 200
    assert first.json()["status"] == "applied"
    assert first.json()["idempotent"] is False
    assert second.status_code == 200
    assert second.json()["status"] == "already_resolved"
    assert second.json()["idempotent"] is True

    async with session_factory() as session:
        balance = await session.scalar(
            select(StockBalance).where(StockBalance.sku_id == stock.sku_id)
        )
        audits = await session.scalar(select(func.count()).select_from(OpsAuditLog))
        actions = (await session.scalars(select(OpsAuditLog.action))).all()
    assert balance is not None
    assert balance.qty_available == 4
    assert audits == 2
    assert set(actions) == {"dlq.retry"}


async def test_stock_audit_does_not_add_full_into_shared(ops_client, seed):
    stock = await seed.simple_stock(qty=5, safety_threshold=2, full_qty=100)
    await seed.add_listing(
        sku_id=stock.sku_id,
        company_id=stock.company_id,
        channel=Channel.MERCADO_LIVRE.value,
        external_item_id="SHARED-AD",
        is_full=False,
        is_active=True,
        last_synced_qty=1,
    )
    await seed.add_listing(
        sku_id=stock.sku_id,
        company_id=stock.company_id,
        channel=Channel.MERCADO_LIVRE.value,
        external_item_id="FULL-AD",
        is_full=True,
        is_active=True,
        last_synced_qty=100,
    )
    response = await ops_client.get("/ops/stock-audit", headers=_auth())
    assert response.status_code == 200
    items = {item["external_item_id"]: item for item in response.json()["items"]}
    shared = items["SHARED-AD"]
    full = items["FULL-AD"]
    assert shared["source_qty_available"] == 5
    assert shared["expected_remote_qty"] == 3
    assert shared["in_sync"] is False
    assert shared["source_warehouse_code"] == "galpao_central"
    assert shared["full_isolated"] is False
    assert full["source_qty_available"] == 100
    assert full["expected_remote_qty"] == 100
    assert full["in_sync"] is True
    assert full["full_isolated"] is True
    assert full["decision"] == "not_synced_by_shared_worker"
