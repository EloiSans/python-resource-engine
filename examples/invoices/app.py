"""Runnable example: ``uv run uvicorn invoices.app:app --app-dir examples --reload``.

Every request needs a bearer token from Keycloak (``docker compose -f
compose.emulators.yaml up -d keycloak``; realm ``invoices``, users ``eli``,
``pau``, ``ada``, ``gina`` and ``root``, password ``dev``; see
docs/example-app.md). Records belong to the caller's organization (the token's
``organization`` claim): ACME users never see Globex records.
``KEYCLOAK_URL`` and ``KEYCLOAK_REALM`` point elsewhere.

Documents go to Garage/S3 when ``S3_ENDPOINT`` is set (see compose.emulators.yaml),
otherwise to an in-memory store (lost on restart; for trying things out only)::

    S3_ENDPOINT=http://127.0.0.1:3910 S3_BUCKET=resource-engine \
    AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... uv run uvicorn ...
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from resource_engine import ResourceManager
from resource_engine.authz import PolicyAuthorizer, system_principal
from resource_engine.messaging import OutboxDelivery
from resource_engine.ports.identity import Authenticator
from resource_engine_fastapi import bearer_actor, mount_asyncapi, mount_resources
from resource_engine_sqlalchemy import unit_of_work_factory
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from invoices.authz import seed
from invoices.bootstrap import authz, tenant_registry
from invoices.infrastructure.persistence import engine as engine_tables
from invoices.infrastructure.persistence import map_entities, metadata


def keycloak_authenticator() -> Authenticator:
    """Tokens of the example realm: roles from the realm, the tenant from the
    user's organization."""
    from resource_engine_oidc import keycloak

    return keycloak(
        os.getenv("KEYCLOAK_URL", "http://127.0.0.1:8180"),
        os.getenv("KEYCLOAK_REALM", "invoices"),
        audience="invoices-api",
        tenant_claim="organization",
    )


def build_manager(session_factory: async_sessionmaker, *, blob_store: Any) -> ResourceManager:
    """The manager the app and the worker share: SQL, permissions, audit, and
    events through the outbox (the worker feeds them to the processes)."""
    registry = tenant_registry
    uow = unit_of_work_factory(
        session_factory,
        registry,
        outbox_table=engine_tables.outbox,
        inbox_table=engine_tables.inbox,
        idempotency_table=engine_tables.idempotency,
    )
    return ResourceManager(
        registry,
        uow,
        blob_store=blob_store,
        authorizer=PolicyAuthorizer(authz, uow),  # system roles + policies as data
        event_delivery=OutboxDelivery(),  # events stored in the change's transaction
        audit=True,  # audit_log table, same transaction as each change
    )


def create_app(
    engine: AsyncEngine | None = None, *, authenticator: Authenticator | None = None
) -> FastAPI:
    map_entities()
    registry = tenant_registry
    engine = engine or create_async_engine(
        os.getenv("DATABASE_URL", "sqlite+aiosqlite:///invoices.db")
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
        await seed(manager, system_principal("invoices-seed"))  # roles and policies
        yield
        await engine.dispose()

    blob_store, open_store = _blob_store()

    @asynccontextmanager
    async def lifespan_with_store(app: FastAPI):
        async with open_store(), lifespan(app):
            yield

    app = FastAPI(title="Invoices (Resource Engine example)", lifespan=lifespan_with_store)
    manager = build_manager(session_factory, blob_store=blob_store)
    app.state.manager = manager
    actor = bearer_actor(authenticator or keycloak_authenticator())
    mount_resources(app, registry, manager, actor_dependency=actor, access_log=True)
    mount_asyncapi(app, registry)  # GET /asyncapi.json: the events it publishes
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
