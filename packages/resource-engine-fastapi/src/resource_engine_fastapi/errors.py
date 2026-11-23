from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from resource_engine import DomainError, ResourceEngineError
from resource_engine import errors as e

STATUS_BY_ERROR: list[tuple[type[ResourceEngineError], int]] = [
    (e.AuthenticationFailed, 401),
    (e.PermissionDenied, 403),
    (e.PrivilegeEscalation, 403),
    (e.InvalidPolicy, 422),
    (e.ValidationFailed, 422),
    (e.InvalidQuery, 400),
    (e.EntityNotFound, 404),
    (e.ResourceNotRegistered, 404),
    (e.UnknownTransition, 404),
    (e.ActionNotFound, 404),
    (e.OperationNotEnabled, 405),
    (e.TransitionNotAllowed, 409),
    (e.DocumentSlotNotFound, 404),
    (e.BlobNotFound, 404),
    (e.ContentTypeNotAllowed, 415),
    (e.DocumentTooLarge, 413),
    (e.UploadNotVerified, 409),
    (e.DocumentNotAvailable, 409),
    (e.RequiredDocumentMissing, 409),
    (e.StorageFailed, 502),
    (e.ConfigurationError, 500),
    (e.RemoteCallFailed, 502),
]


def status_for(exc: ResourceEngineError) -> int:
    for cls, status in STATUS_BY_ERROR:
        if isinstance(exc, cls):
            return status
    return 500


def install_exception_handlers(app: FastAPI) -> None:
    """Map engine and domain errors to JSON responses ``{"error": {...}}``."""

    @app.exception_handler(ResourceEngineError)
    async def _engine_error(request: Request, exc: ResourceEngineError) -> JSONResponse:
        headers = None
        if isinstance(exc, e.AuthenticationFailed):
            reason = exc.reason if exc.reason != "missing" else None
            headers = {
                "WWW-Authenticate": "Bearer"
                + (f' error="invalid_token", error_description="{reason}"' if reason else "")
            }
        return JSONResponse(
            status_code=status_for(exc), content={"error": exc.to_dict()}, headers=headers
        )

    @app.exception_handler(DomainError)
    async def _domain_error(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"error": exc.to_dict()})
