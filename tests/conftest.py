"""Pytest fixtures against a real PostgreSQL.

Set TEST_DATABASE_URL to a database whose name contains ``test``. The session
fixture drops and recreates the public schema, then applies Alembic migrations.
When TEST_DATABASE_URL is unset and Docker is available, testcontainers starts
PostgreSQL 16 instead.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.factories import Seed  # noqa: E402


def to_asyncpg_sqlalchemy_url(url: str) -> str:
    for prefix in (
        "postgresql+asyncpg://",
        "postgresql+psycopg2://",
        "postgresql+psycopg://",
    ):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix) :]
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


def to_asyncpg_dsn(url: str) -> str:
    return to_asyncpg_sqlalchemy_url(url).replace("postgresql+asyncpg://", "postgresql://", 1)


def database_name(url: str) -> str:
    return urlparse(to_asyncpg_dsn(url)).path.lstrip("/").split("?")[0]


async def reset_public_schema(url: str) -> None:
    connection = await asyncpg.connect(to_asyncpg_dsn(url))
    try:
        await connection.execute("DROP SCHEMA IF EXISTS public CASCADE")
        await connection.execute("CREATE SCHEMA public")
        await connection.execute("GRANT ALL ON SCHEMA public TO public")
    finally:
        await connection.close()


def migrate(url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = url
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "alembic upgrade head failed\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )


@pytest.fixture(scope="session")
def database_url() -> str:
    configured = os.environ.get("TEST_DATABASE_URL")
    if configured:
        url = to_asyncpg_sqlalchemy_url(configured)
        name = database_name(url)
        if "test" not in name:
            raise RuntimeError(
                f"Refusing to reset database {name!r}. TEST_DATABASE_URL must use a "
                "database name that contains 'test'."
            )
        asyncio.run(reset_public_schema(url))
        migrate(url)
        yield url
        return

    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError as exc:
        pytest.exit(
            "Set TEST_DATABASE_URL to a real Postgres test database, or install "
            f"Docker plus testcontainers ({exc}).",
            returncode=2,
        )

    container = PostgresContainer("postgres:16-alpine")
    container.start()
    try:
        url = to_asyncpg_sqlalchemy_url(container.get_connection_url())
        migrate(url)
        yield url
    finally:
        container.stop()


@pytest.fixture(scope="session")
def redis_url() -> str:
    return os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")


@pytest.fixture
async def engine(database_url: str):
    eng = create_async_engine(database_url, pool_size=10, max_overflow=10)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
async def clean_tables(engine):
    await _truncate(engine)
    yield
    await _truncate(engine)


async def _truncate(engine) -> None:
    from sqlalchemy import text

    statement = text(
        """
        TRUNCATE TABLE
            ops_audit_log,
            dlq_events,
            invoices,
            order_items,
            orders,
            processed_events,
            listing_links,
            stock_balances,
            kit_components,
            skus,
            warehouses,
            company_channel_secrets,
            companies
        RESTART IDENTITY CASCADE
        """
    )
    async with engine.begin() as connection:
        await connection.execute(statement)


@pytest.fixture
async def seed(session_factory) -> Seed:
    return Seed(session_factory)


@pytest.fixture
async def redis_client(redis_url: str):
    from redis.asyncio import Redis

    from erp.domain.enums import STOCK_SYNC_QUEUE_KEY

    client = Redis.from_url(redis_url, decode_responses=True)
    await client.delete(STOCK_SYNC_QUEUE_KEY)
    yield client
    await client.delete(STOCK_SYNC_QUEUE_KEY)
    await client.aclose()
