from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from resource_engine import DomainError, ResourceEngineError
from resource_engine import errors as e

STATUS_BY_ERROR: list[tuple[type[ResourceEngineError], int]] = [
    (e.AuthenticationFailed, 401),
    (e.PermissionDenied, 403),
    (e.PrivilegeEscalation, 403),
    (e.FieldNotWritable, 403),
    (e.FieldNotReadable, 403),
    (e.TenantRequired, 403),
    (e.TenantMismatch, 403),
    (e.InvalidPolicy, 422),
    (e.ValidationFailed, 422),
    (e.InvalidQuery, 400),
    (e.EntityNotFound, 404),
    (e.ResourceNotRegistered, 404),
    (e.UnknownTransition, 404),
    (e.ActionNotFound, 404),
    (e.OperationNotEnabled, 405),
    (e.TransitionNotAllowed, 409),
    (e.VersionConflict, 409),
    (e.VersionRequired, 428),
    (e.IdempotencyKeyReused, 422),
    (e.ReferenceNotFound, 422),
    (e.ReferenceInUse, 409),
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
    (e.RateLimited, 429),
]


def status_for(exc: ResourceEngineError) -> int:
    for cls, status in STATUS_BY_ERROR:
        if isinstance(exc, cls):
            return status
    return 500


def install_exception_handlers(app: FastAPI) -> None:
    """Map engine and domain errors to JSON responses ``{"error": {...}}``.

    5xx answers (engine failures, and any other exception as a 500 ``RE-E000``
    "Internal error") are reported (``resource_engine.reporting``)."""

    @app.exception_handler(ResourceEngineError)
    async def _engine_error(request: Request, exc: ResourceEngineError) -> JSONResponse:
        status = status_for(exc)
        if status >= 500:
            await _report(request, exc, status)
        headers = None
        if isinstance(exc, e.AuthenticationFailed):
            reason = exc.reason if exc.reason != "missing" else None
            headers = {
                "WWW-Authenticate": "Bearer"
                + (f' error="invalid_token", error_description="{reason}"' if reason else "")
            }
        elif isinstance(exc, e.RateLimited):
            details = exc.details if isinstance(exc.details, dict) else {}
            retry = int(details.get("retry_after") or 1)
            headers = {
                "Retry-After": str(retry),
                "RateLimit-Limit": str(details.get("limit")),
                "RateLimit-Remaining": "0",
                "RateLimit-Reset": str(retry),
            }
        return JSONResponse(status_code=status, content={"error": exc.to_dict()}, headers=headers)

    @app.exception_handler(DomainError)
    async def _domain_error(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"error": exc.to_dict()})

    app.add_middleware(UnexpectedErrorMiddleware)
    app.add_middleware(RateLimitHeadersMiddleware)


class RateLimitHeadersMiddleware:
    """``RateLimit-Limit``, ``-Remaining`` and ``-Reset`` on responses to callers
    the actor dependency rate-limited (``bearer_actor(..., rate_limiter=)``)."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def with_headers(message: Any) -> None:
            if message["type"] == "http.response.start":
                decision = (scope.get("state") or {}).get("rate_limit")
                if decision is not None:
                    names = {k.lower() for k, _ in message.get("headers", [])}
                    if b"ratelimit-limit" not in names:
                        message["headers"] = [
                            *message.get("headers", []),
                            (b"ratelimit-limit", str(decision.limit).encode()),
                            (b"ratelimit-remaining", str(decision.remaining).encode()),
                            (b"ratelimit-reset", str(decision.reset).encode()),
                        ]
            await send(message)

        await self.app(scope, receive, with_headers)


class UnexpectedErrorMiddleware:
    """Any other exception: reported, answered with 500 ``RE-E000``.

    A middleware rather than an ``Exception`` handler (which Starlette runs
    outside every middleware), so the report and the response stay inside the
    request: its request id and access log line."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracking(message: dict[str, Any]) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking)
        except Exception as exc:
            if started:
                raise
            await _report(Request(scope), exc, 500)
            response = JSONResponse(
                status_code=500,
                content={"error": {"code": "RE-E000", "message": "Internal error"}},
            )
            await response(scope, receive, send)


async def _report(request: Request, exc: BaseException, status: int) -> None:
    """Report a 5xx with the route (not the raw path: ids stay out of grouping)."""
    from resource_engine.reporting import report_error

    route = getattr(request.scope.get("route"), "path", None) or request.url.path
    await report_error(
        exc,
        where="http",
        force=True,
        actor=getattr(request.state, "principal", None),
        method=request.method,
        route=route,
        status=status,
    )
