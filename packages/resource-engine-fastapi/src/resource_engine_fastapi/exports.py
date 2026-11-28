"""Background exports and printouts over HTTP.

- ``POST /<resource>/exports`` (in the resource's router) queues an export.
- ``GET /exports/{id}``: its status; ``GET /exports/{id}/content``: the file
  (or a 307 to a presigned URL with ``redirect_downloads``).
- ``GET /<resource>/{id}/printouts/<name>?format=pdf``: a document rendered
  from the record (``Resource.printouts``), not stored.
"""

from __future__ import annotations

import inspect
from typing import Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Response
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field
from resource_engine import ResourceDefinition, ResourceManager
from resource_engine.documents import content_disposition
from resource_engine.generation import CONTENT_TYPES
from resource_engine.ports.storage import SupportsPresignedUrls

_KW = inspect.Parameter.KEYWORD_ONLY


class ExportError(BaseModel):
    code: str
    message: str


class ExportJobOut(BaseModel):
    """A background export."""

    id: str = Field(description="Export id")
    resource: str
    format: Literal["csv", "xlsx"]
    status: Literal["pending", "running", "done", "failed"] = Field(
        description="pending, running, done (download it) or failed (see error)"
    )
    rows: int | None = Field(default=None, description="Rows that matched when requested")
    filename: str | None = None
    size: int | None = Field(default=None, description="Size of the file in bytes")
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    expires_at: str | None = Field(default=None, description="When the file is deleted")
    error: ExportError | None = None


def build_export_router(
    manager: ResourceManager, *, actor_dependency: Any, redirect_downloads: bool = False
) -> APIRouter:
    exports = manager.exports
    assert exports is not None
    router = APIRouter(prefix=f"/{exports.path}", tags=[exports.path])

    @router.get(
        "/{export_id}",
        response_model=ExportJobOut,
        summary="Status of a background export",
        operation_id="get_export",
    )
    async def get_export(export_id: str = Path(...), actor: Any = Depends(actor_dependency)) -> Any:
        return (await exports.get(export_id, actor=actor)).to_dict()

    @router.get(
        "/{export_id}/content",
        response_class=StreamingResponse,
        summary="Download a finished export",
        operation_id="download_export",
        responses={
            200: {
                "content": {
                    "text/csv": {},
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {},
                }
            },
            307: {"description": "Redirect to a presigned URL"},
        },
    )
    async def download_export(
        export_id: str = Path(...), actor: Any = Depends(actor_dependency)
    ) -> Response:
        if redirect_downloads and isinstance(exports.store, SupportsPresignedUrls):
            url = await exports.download_url(export_id, actor=actor)
            return RedirectResponse(url.url, status_code=307)
        job, stream = await exports.open(export_id, actor=actor)
        from resource_engine.exports import MEDIA_TYPES

        headers = {
            "Content-Disposition": content_disposition(job.filename or f"{job.id}.{job.format}"),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        }
        if job.size is not None:
            headers["Content-Length"] = str(job.size)
        return StreamingResponse(stream, media_type=MEDIA_TYPES[job.format], headers=headers)

    return router


def _printout_endpoint(manager: ResourceManager, resource: str, name: str) -> Any:
    async def endpoint(**kw: Any) -> Response:
        document, filename = await manager.generator.printout(
            manager,
            resource,
            kw["entity_id"],
            name,
            format=kw.get("format"),
            locale=kw.get("locale"),
            actor=kw.get("actor"),
            tenant=kw.get("tenant"),
        )
        return Response(
            document.content,
            media_type=document.content_type,
            headers={
                "Content-Disposition": content_disposition(filename),
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
            },
        )

    return endpoint


def printout_routes(
    router: APIRouter,
    definition: ResourceDefinition,
    manager: ResourceManager,
    id_param: inspect.Parameter,
    tenant_params: list[inspect.Parameter],
    actor_param: inspect.Parameter,
) -> None:
    """``GET /{id}/printouts/<name>`` for each of the resource's printouts."""
    printouts = definition.resource_cls.printouts
    if not printouts or not definition.exposed("get"):
        return
    resource = definition.name
    for printout_name, printout in printouts.items():
        formats = tuple(printout.formats)
        endpoint = _printout_endpoint(manager, resource, printout_name)
        endpoint.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
            [
                id_param,
                inspect.Parameter(
                    "format",
                    _KW,
                    annotation=Literal[formats],  # type: ignore[valid-type]
                    default=Query(formats[0], description=f"One of {', '.join(formats)}"),
                ),
                inspect.Parameter(
                    "locale",
                    _KW,
                    annotation=str | None,
                    default=Query(None, description="Language of the document (ca, es, en...)"),
                ),
                *tenant_params,
                actor_param,
            ]
        )
        endpoint.__name__ = f"printout_{resource}_{printout_name}"
        router.add_api_route(
            f"/{{entity_id}}/printouts/{printout_name}",
            endpoint,
            methods=["GET"],
            response_class=Response,
            responses={200: {"content": {CONTENT_TYPES[f].split(";")[0]: {} for f in formats}}},
            summary=f"Print {printout_name} for one of {resource}",
            description=printout.description
            or f"The {printout_name} document of the record, rendered now and not stored. "
            "Fields the caller may not read are left out.",
        )
