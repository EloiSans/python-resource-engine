"""Runnable example: ``uv run uvicorn invoices.app:app --app-dir examples --reload``.

Documents go to Garage/S3 when ``S3_ENDPOINT`` is set (see compose.emulators.yaml),
otherwise to an in-memory store (lost on restart; for trying things out only)::

    S3_ENDPOINT=http://127.0.0.1:3910 S3_BUCKET=resource-engine \
    AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... uv run uvicorn ...
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from resource_engine import ResourceManager
from resource_engine_fastapi import mount_resources
from resource_engine_sqlalchemy import unit_of_work_factory
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from invoices.bootstrap import registry
from invoices.infrastructure.persistence import map_entities, metadata


def create_app(engine: AsyncEngine | None = None) -> FastAPI:
    map_entities()
    engine = engine or create_async_engine(
        os.getenv("DATABASE_URL", "sqlite+aiosqlite:///invoices.db")
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
        yield
        await engine.dispose()

    blob_store, open_store = _blob_store()

    @asynccontextmanager
    async def lifespan_with_store(app: FastAPI):
        async with open_store(), lifespan(app):
            yield

    app = FastAPI(title="Invoices (Resource Engine example)", lifespan=lifespan_with_store)
    manager = ResourceManager(
        registry, unit_of_work_factory(session_factory, registry), blob_store=blob_store
    )
    mount_resources(app, registry, manager)
    return app


def _blob_store():
    """The store, and a context manager that connects it for the app's lifetime."""
    endpoint = os.getenv("S3_ENDPOINT")
    if not endpoint:
        from contextlib import nullcontext

        from resource_engine.testing import InMemoryBlobStore

        return InMemoryBlobStore(), lambda: nullcontext()

    from aiobotocore.session import get_session
    from resource_engine_s3 import S3BlobStore, s3_client_options

    store = S3BlobStore(None, os.getenv("S3_BUCKET", "resource-engine"))

    @asynccontextmanager
    async def connect():
        options = s3_client_options(
            endpoint, region=os.getenv("S3_REGION", "garage"), path_style=True
        )
        async with get_session().create_client("s3", **options) as client:
            store.client = client  # the aiobotocore client must live in the app's loop
            yield

    return store, connect
