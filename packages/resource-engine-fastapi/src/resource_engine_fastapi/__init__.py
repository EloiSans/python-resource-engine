"""FastAPI adapter for Resource Engine."""

from resource_engine_fastapi.access import AccessLogMiddleware, install_access_log
from resource_engine_fastapi.asyncapi import mount_asyncapi
from resource_engine_fastapi.auth import bearer_actor, bearer_token
from resource_engine_fastapi.errors import install_exception_handlers, status_for
from resource_engine_fastapi.events import build_event_receiver, default_decoder
from resource_engine_fastapi.router import build_resource_router, build_router, mount_resources

__all__ = [
    "AccessLogMiddleware",
    "bearer_actor",
    "bearer_token",
    "build_event_receiver",
    "build_resource_router",
    "build_router",
    "default_decoder",
    "install_access_log",
    "install_exception_handlers",
    "mount_asyncapi",
    "mount_resources",
    "status_for",
]
