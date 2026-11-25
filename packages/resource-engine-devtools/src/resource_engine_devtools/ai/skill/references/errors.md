# Error codes

Every engine error has a stable `code`, a `message`, a `hint` (what to change)
and a `doc` link. Read the hint first.

## Raised while declaring (fix the code)

| Code | Class | Fix |
|---|---|---|
| RE-E001 | `ConfigurationError` | A resource, action, hook or option is declared wrongly; the message names it |
| RE-E004 | `StateFieldExposed` | Remove the state field from the `create` / `update` schema; change it with transitions |
| RE-E005 | `VersioningNotSupported` | `version_field` on a repository without compare-and-set (REST, SOAP): drop it |
| RE-E008 | `IdempotencyNotSupported` | Pass `idempotency_table=` to the unit of work factory (`engine_tables(metadata, idempotency=True)`) |
| RE-E009 | `HistoryNotAvailable` | `history` needs a state machine, `audit` on the resource and `audit=True` (a `RepositoryAuditSink`) on the manager |
| RE-E104 | `ExternalEffectInTransaction` | Move the `@external_effect` hook to `after_commit` or record an event |

## Raised by operations

| Code | Class | HTTP | Meaning |
|---|---|---|---|
| RE-E002 | `ResourceNotRegistered` | 404 | Unknown resource name |
| RE-E003 | `OperationNotEnabled` | 405 | The operation is not in `operations` |
| RE-E007 | `TransactionDoomed` | 500 | An operation failed inside `manager.transaction()` |
| RE-E101 | `ValidationFailed` | 422 | Input does not match the schema (`details`: Pydantic errors) |
| RE-E102 | `EntityNotFound` | 404 | No record with that id (or not visible) |
| RE-E103 | `InvalidQuery` | 400 | Filter or order field not in `filterable` / `sortable` |
| RE-E105 | `VersionConflict` | 409 | Stale version: reload and retry |
| RE-E106 | `VersionRequired` | 428 | `require_version`: send `If-Match` / `expected_version` |
| RE-E107 | `IdempotencyKeyReused` | 422 | The idempotency key was used for a different request |
| RE-E108 | `ReferenceNotFound` | 422 | A reference points to a missing or unreadable record: use an existing id |
| RE-E109 | `ReferenceInUse` | 409 | Other records still reference it: change or delete them first |
| RE-E201 | `TransitionNotAllowed` | 409 | Not possible from the current state (`details.allowed`) |
| RE-E202 | `UnknownTransition` | 404 | No such transition |
| RE-E301 | `ActionNotFound` | 404 | No such action |
| RE-E401 | `PublishFailed` | – | A broker refused messages |
| RE-E402 | `NoHandler` | – | No consumer handler for the event type |
| RE-E403 | `OutboxNotSupported` | 500 | `OutboxDelivery` without an outbox table |
| RE-E404 | `InboxNotSupported` | – | A consumer's unit of work has no inbox table |
| RE-E405 | `UndecodableMessage` | 422 | Inbound body is not an envelope or CloudEvent |
| RE-E406 | `MessageRejected` | 422 | Raised by a handler: dead-letter without retries |
| RE-E501 | `RemoteCallFailed` | 502 | A REST or SOAP service failed |
| RE-E601 | `DocumentSlotNotFound` | 404 | No such document slot |
| RE-E602 | `ContentTypeNotAllowed` | 415 | The slot does not accept the type |
| RE-E603 | `DocumentTooLarge` | 413 | Over `max_bytes` |
| RE-E604 | `UploadNotVerified` | 409 | Presigned upload missing or expired |
| RE-E605 | `DocumentNotAvailable` | 409 | Document pending, processing, rejected or deleted |
| RE-E606 | `RequiredDocumentMissing` | 409 | The transition needs a document (`required_for`) |
| RE-E607 | `StorageFailed` | 502 | The blob store failed |
| RE-E608 | `BlobNotFound` | 404 | No content under the key |
| RE-E701 | `AuthenticationFailed` | 401 | Missing or invalid credentials |
| RE-E702 | `PermissionDenied` | 403 | Not allowed (`details.action`, `reason`) |
| RE-E703 | `InvalidPolicy` | 422 | Invalid policy, role or assignment (`details.problems`) |
| RE-E704 | `PrivilegeEscalation` | 403 | Granting actions the editor does not hold |
| RE-E705 | `ConditionNotSupported` | 500 | A row filter cannot be applied (fails closed) |
| RE-E706 | `FieldNotWritable` | 403 | Input sets fields the caller may not write |
| RE-E707 | `FieldNotReadable` | 403 | Filter or sort by a field the caller may not read |
| RE-E708 | `TenantRequired` | 403 | Tenant-scoped resource without a tenant |
| RE-E709 | `TenantMismatch` | 403 | Another tenant named |

Your own business errors subclass `resource_engine.domain.DomainError`, set a
`code`, and map to HTTP 422.

## Tool rules

| Code | Tool | Fix |
|---|---|---|
| RE-C001 | `check` | Remove the infrastructure import from `domain/` |
| RE-C002 | `check` | Move the external effect after commit (event, consumer, `after_commit` + `@external_effect`) |
| RE-C003 | `check` | Do it before commit, or start a new operation |
| RE-C004 | `check` | Grant the operation: a seed policy, a system role, or remove the operation |
| RE-C005 | `check` | The engine refused a declaration: read the `RE-E` code and hint in the message |
| RE-C006 | `check` | Add the recorded event class to the resource's `events` |
| RE-D001…D011 | `docs validate` | Add the missing docstring or section ([docstrings.md](docstrings.md)) |
