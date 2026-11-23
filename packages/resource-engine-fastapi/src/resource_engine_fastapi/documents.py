"""HTTP routes for documents attached to resources.

Owner routes (``/{resource}/{id}/documents...``) are added for resources with
document slots; download and confirmation live under the documents resource.
Annotations are evaluated eagerly here (no ``from __future__ import annotations``):
FastAPI needs the per-resource id and slot types defined inside the builder.

With an authorizer, the manager checks ``<resource>:attach|detach|download:<slot>``
and the owner's visibility.
"""

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Path, Query, Request, Response, UploadFile, status
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field
from resource_engine import ResourceDefinition, ResourceManager
from resource_engine.documents import DocumentRead, content_disposition
from resource_engine.errors import DocumentTooLarge
from resource_engine.ports.storage import SupportsPresignedUrls

CHUNK = 1024 * 1024
MULTIPART_OVERHEAD = 16 * 1024


class UploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=1000, description="Original file name")
    content_type: str = Field(min_length=3, max_length=255, description="MIME type")
    size: int | None = Field(default=None, ge=0, description="Size in bytes, if known")


class PresignedUpload(BaseModel):
    url: str
    method: str
    headers: dict[str, str]
    expires_at: datetime


class UploadTicketOut(BaseModel):
    document: DocumentRead
    upload: PresignedUpload


async def _chunks(file: UploadFile):
    while chunk := await file.read(CHUNK):
        yield chunk


def add_owner_document_routes(
    router: APIRouter,
    definition: ResourceDefinition,
    manager: ResourceManager,
    actor_dependency: Any,
) -> None:
    """``/{id}/documents`` routes on the owner resource's router."""
    if manager.documents is None or not definition.document_slots:
        return
    docs = manager.documents
    name = definition.name
    slots = definition.document_slots
    SlotName = Literal[tuple(slots)]  # type: ignore[valid-type]  # OpenAPI enum
    IdType = definition.id_type

    @router.get(
        "/{entity_id}/documents",
        response_model=list[DocumentRead],
        summary=f"Documents of a {name} entity",
        operation_id=f"{name}_documents",
    )
    async def list_documents(
        entity_id: IdType = Path(...),  # type: ignore[valid-type]
        slot: SlotName | None = Query(None),  # type: ignore[valid-type]
        actor: Any = Depends(actor_dependency),
    ) -> Any:
        return await docs.list(name, entity_id, slot, actor=actor)

    if any(s.allows("api") for s in slots.values()):

        @router.post(
            "/{entity_id}/documents/{slot}",
            response_model=DocumentRead,
            status_code=status.HTTP_201_CREATED,
            summary=f"Upload a document to a {name} entity",
            operation_id=f"{name}_attach_document",
        )
        async def attach_document(
            request: Request,
            entity_id: IdType = Path(...),  # type: ignore[valid-type]
            slot: SlotName = Path(...),  # type: ignore[valid-type]
            file: UploadFile = File(...),
            actor: Any = Depends(actor_dependency),
        ) -> Any:
            declared = request.headers.get("content-length")
            limit = slots[slot].max_bytes
            if declared and declared.isdigit() and int(declared) > limit + MULTIPART_OVERHEAD:
                raise DocumentTooLarge(
                    f"The request has {declared} bytes; slot {slot!r} allows {limit}",
                    details={"max_bytes": limit},
                )
            return await docs.attach(
                name,
                entity_id,
                slot,
                _chunks(file),
                filename=file.filename or "file",
                content_type=file.content_type or "application/octet-stream",
                actor=actor,
            )

    if isinstance(docs.store, SupportsPresignedUrls) and any(
        s.allows("presigned") for s in slots.values()
    ):

        @router.post(
            "/{entity_id}/documents/{slot}/uploads",
            response_model=UploadTicketOut,
            status_code=status.HTTP_201_CREATED,
            summary="Request a direct (presigned) upload",
            description="PUT the file to `upload.url` with `upload.headers`, then POST "
            "`/documents/{id}/confirm`.",
            operation_id=f"{name}_request_upload",
        )
        async def request_upload(
            payload: UploadRequest,
            entity_id: IdType = Path(...),  # type: ignore[valid-type]
            slot: SlotName = Path(...),  # type: ignore[valid-type]
            actor: Any = Depends(actor_dependency),
        ) -> Any:
            ticket = await docs.request_upload(
                name,
                entity_id,
                slot,
                filename=payload.filename,
                content_type=payload.content_type,
                size=payload.size,
                actor=actor,
            )
            return UploadTicketOut(
                document=ticket.document,
                upload=PresignedUpload(
                    url=ticket.upload.url,
                    method=ticket.upload.method,
                    headers=ticket.upload.headers,
                    expires_at=ticket.upload.expires_at,
                ),
            )

    @router.delete(
        "/{entity_id}/documents/{document_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        response_class=Response,
        summary="Detach a document",
        operation_id=f"{name}_detach_document",
    )
    async def detach_document(
        entity_id: IdType = Path(...),  # type: ignore[valid-type]
        document_id: str = Path(...),
        actor: Any = Depends(actor_dependency),
    ) -> Response:
        await docs.detach(name, entity_id, document_id, actor=actor)
        return Response(status_code=204)


def build_document_content_router(
    manager: ResourceManager,
    *,
    actor_dependency: Any,
    redirect_downloads: bool = False,
) -> APIRouter:
    """``/documents/{id}/content`` and ``/documents/{id}/confirm``."""
    docs = manager.documents
    assert docs is not None
    router = APIRouter(prefix=f"/{docs.resource}", tags=[docs.resource])

    @router.get(
        "/{document_id}/content",
        summary="Download a document",
        response_class=StreamingResponse,
        responses={307: {"description": "Redirect to a presigned URL"}},
    )
    async def download(
        document_id: str = Path(...), actor: Any = Depends(actor_dependency)
    ) -> Response:
        if redirect_downloads and isinstance(docs.store, SupportsPresignedUrls):
            url = await docs.download_url(document_id, actor=actor)
            return RedirectResponse(url.url, status_code=307)
        document, stream = await docs.open(document_id, actor=actor)
        headers = {
            "Content-Disposition": content_disposition(document.filename),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        }
        if document.size is not None:
            headers["Content-Length"] = str(document.size)
        return StreamingResponse(stream, media_type=document.content_type, headers=headers)

    @router.post(
        "/{document_id}/confirm",
        response_model=DocumentRead,
        summary="Confirm a presigned upload",
    )
    async def confirm(document_id: str = Path(...), actor: Any = Depends(actor_dependency)) -> Any:
        return await docs.confirm_upload(document_id, actor=actor)

    return router
