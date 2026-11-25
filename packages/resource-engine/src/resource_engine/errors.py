"""Engine errors with stable codes.

Every error raised by the engine carries:

- ``code``: a stable identifier (``RE-Exxx``) that tools and agents can match on.
- ``message``: what went wrong.
- ``hint``: how to fix it.
- ``doc``: where to read more.

The catalogue lives in ``docs/errors.md``. Codes are never reused once published.
"""

from __future__ import annotations

from typing import Any, ClassVar

DOCS_BASE = "docs/errors.md"


class ResourceEngineError(Exception):
    """Base class for every error raised by the engine."""

    code: ClassVar[str] = "RE-E000"

    def __init__(
        self,
        message: str,
        *,
        hint: str | None = None,
        doc: str | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.doc = doc or f"{DOCS_BASE}#{self.code.lower()}"
        self.details = details

    def __str__(self) -> str:
        parts = [f"{self.code}: {self.message}"]
        if self.hint:
            parts.append(self.hint)
        if self.doc:
            parts.append(f"See {self.doc}")
        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.hint:
            data["hint"] = self.hint
        if self.doc:
            data["doc"] = self.doc
        if self.details is not None:
            data["details"] = self.details
        return data


# --- Configuration (RE-E0xx): raised while building definitions, at bootstrap ---


class ConfigurationError(ResourceEngineError):
    """A resource, action or hook is declared incorrectly."""

    code = "RE-E001"


class ResourceNotRegistered(ResourceEngineError):
    code = "RE-E002"


class OperationNotEnabled(ResourceEngineError):
    code = "RE-E003"


class StateFieldExposed(ConfigurationError):
    """The state field appears in a create/update schema."""

    code = "RE-E004"


class ExternalEffectInTransaction(ConfigurationError):
    """A hook marked as producing external effects is registered before commit."""

    code = "RE-E104"


# --- Input and lookup (RE-E1xx) ---


class VersioningNotSupported(ConfigurationError):
    """A versioned resource (``version_field``) uses a repository that cannot
    compare and set versions (``supports_versions``)."""

    code = "RE-E005"


class TransactionDoomed(ResourceEngineError):
    """An operation failed inside ``manager.transaction()``: the transaction can
    only roll back. ``details`` carries the original error's ``code`` and
    ``message``; the exception is chained to it."""

    code = "RE-E007"


class IdempotencyNotSupported(ConfigurationError):
    """An idempotency key was sent but the unit of work has no idempotency store."""

    code = "RE-E008"


class HistoryNotAvailable(ConfigurationError):
    """Transition history was asked for a resource without a state machine, or
    without an audit trail stored as a resource."""

    code = "RE-E009"


class ValidationFailed(ResourceEngineError):
    code = "RE-E101"


class EntityNotFound(ResourceEngineError):
    code = "RE-E102"


class InvalidQuery(ResourceEngineError):
    code = "RE-E103"


# --- State machines (RE-E2xx) ---


class VersionConflict(ResourceEngineError):
    """The record changed since the client read it (optimistic concurrency).

    ``details``: ``resource``, ``id``, ``expected`` (the version the write was
    based on) and ``current`` (when known). Reload the record and retry.
    """

    code = "RE-E105"


class VersionRequired(ResourceEngineError):
    """The resource requires the version the client read (``If-Match``)."""

    code = "RE-E106"


class IdempotencyKeyReused(ResourceEngineError):
    """The idempotency key was already used by the same caller for a different
    request (another operation, record or input)."""

    code = "RE-E107"


class ReferenceNotFound(ResourceEngineError):
    """A reference field points to a record that does not exist or that the
    caller may not read. ``details``: ``field``, ``resource``, ``id``."""

    code = "RE-E108"


class ReferenceInUse(ResourceEngineError):
    """The record cannot be deleted: other records reference it
    (``on_delete="restrict"``). ``details``: ``resource``, ``field``, ``id``."""

    code = "RE-E109"


class TransitionNotAllowed(ResourceEngineError):
    code = "RE-E201"


class UnknownTransition(ResourceEngineError):
    code = "RE-E202"


# --- Actions (RE-E3xx) ---


class ActionNotFound(ResourceEngineError):
    code = "RE-E301"


# --- Messaging (RE-E4xx) ---


class PublishFailed(ResourceEngineError):
    """Some messages could not be handed to the transport.

    ``failed_ids`` lists the envelope ids that were not accepted; the rest were.
    The outbox relay uses it to mark only the delivered messages as published.
    """

    code = "RE-E401"

    def __init__(self, message: str, *, failed_ids: list[str], **kw: object) -> None:
        super().__init__(message, details={"failed_ids": failed_ids}, **kw)  # type: ignore[arg-type]
        self.failed_ids = failed_ids


class NoHandler(ResourceEngineError):
    """An inbound message has a type no handler is registered for."""

    code = "RE-E402"


class OutboxNotSupported(ConfigurationError):
    """OutboxDelivery is used with a unit of work that has no outbox."""

    code = "RE-E403"


class InboxNotSupported(ConfigurationError):
    """A consumer was given an inbox unit of work factory whose units have no ``inbox()``."""

    code = "RE-E404"


class UndecodableMessage(ResourceEngineError):
    """An inbound message body is not an envelope or a CloudEvent."""

    code = "RE-E405"


class MessageRejected(ResourceEngineError):
    """Raise from a message handler to dead-letter the message without retrying it."""

    code = "RE-E406"


# --- External services (RE-E5xx) ---


class RemoteCallFailed(ResourceEngineError):
    """A call to an external service (REST, SOAP, OData...) failed.

    ``details`` carries ``service``, ``operation``, the upstream ``status`` (HTTP
    status, or ``None`` for transport errors and SOAP faults) and a short ``body``
    excerpt. HTTP adapters map it to 502 Bad Gateway.
    """

    code = "RE-E501"

    def __init__(
        self,
        message: str,
        *,
        service: str,
        operation: str,
        status: int | None = None,
        body: str | None = None,
        **kw: object,
    ) -> None:
        details = {"service": service, "operation": operation, "status": status}
        if body:
            details["body"] = body[:500]
        super().__init__(message, details=details, **kw)  # type: ignore[arg-type]
        self.service = service
        self.operation = operation
        self.status = status


# --- Documents and storage (RE-E6xx) ---


class DocumentSlotNotFound(ResourceEngineError):
    """The resource declares no document slot with that name."""

    code = "RE-E601"


class ContentTypeNotAllowed(ResourceEngineError):
    """The file's content type is not accepted by the slot."""

    code = "RE-E602"


class DocumentTooLarge(ResourceEngineError):
    """The file exceeds the slot's ``max_bytes``."""

    code = "RE-E603"


class UploadNotVerified(ResourceEngineError):
    """A presigned upload cannot be confirmed (missing object, too large, expired)."""

    code = "RE-E604"


class DocumentNotAvailable(ResourceEngineError):
    """The document exists but its content cannot be served in its current state."""

    code = "RE-E605"


class RequiredDocumentMissing(ResourceEngineError):
    """A transition needs a document the entity does not have yet."""

    code = "RE-E606"


class StorageFailed(ResourceEngineError):
    """The blob store failed (network, permissions, bucket missing)."""

    code = "RE-E607"


class BlobNotFound(ResourceEngineError):
    """No blob stored under the key."""

    code = "RE-E608"


# --- Identity (RE-E7xx) ---


class AuthenticationFailed(ResourceEngineError):
    """The caller's credentials are missing, malformed, expired or not trusted.

    ``details.reason`` is a short machine-readable reason (``missing``,
    ``expired``, ``invalid_signature``, ``invalid_audience``, ``invalid_issuer``,
    ``invalid_token``...). Messages never echo the token.
    """

    code = "RE-E701"

    def __init__(self, message: str, *, reason: str, **kw: object) -> None:
        super().__init__(message, details={"reason": reason}, **kw)  # type: ignore[arg-type]
        self.reason = reason


class PermissionDenied(ResourceEngineError):
    """The principal may not perform the action. ``details.action`` names it."""

    code = "RE-E702"


class InvalidPolicy(ResourceEngineError):
    """A policy does not validate against the catalogue; ``details.problems``
    lists ``{path, message}`` entries for the editor."""

    code = "RE-E703"


class PrivilegeEscalation(ResourceEngineError):
    """An editor tried to grant actions they do not hold themselves."""

    code = "RE-E704"


class ConditionNotSupported(ResourceEngineError):
    """A repository cannot apply a row-level filter; the operation fails closed."""

    code = "RE-E705"


class FieldNotWritable(ResourceEngineError):
    """The input sets fields the principal may not write; ``details.fields``
    lists them. Nothing is saved."""

    code = "RE-E706"


class FieldNotReadable(ResourceEngineError):
    """A query filters or sorts by fields the principal may not read;
    ``details.fields`` lists them."""

    code = "RE-E707"


class TenantRequired(ResourceEngineError):
    """A tenant-scoped resource needs a tenant: the caller has none, or a
    cross-tenant caller did not name one where it is required (create)."""

    code = "RE-E708"


class TenantMismatch(ResourceEngineError):
    """The operation names a tenant other than the caller's."""

    code = "RE-E709"
