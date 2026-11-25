# Error catalogue

Codes are stable: once released, a code keeps its meaning. Every error exposes
`code`, `message`, `hint`, `doc` and optionally `details`; the HTTP adapter returns
them as `{"error": {...}}`.

| Code | Class | HTTP | Meaning |
|---|---|---|---|
| <a id="re-e001"></a>RE-E001 | `ConfigurationError` | 500 | A resource, action or hook is declared incorrectly (raised at registration). |
| <a id="re-e002"></a>RE-E002 | `ResourceNotRegistered` | 404 | The resource name is not in the registry. |
| <a id="re-e003"></a>RE-E003 | `OperationNotEnabled` | 405 | The CRUD operation is not listed in `operations`. |
| <a id="re-e004"></a>RE-E004 | `StateFieldExposed` | 500 | The state field appears in a create/update schema. Use transitions. |
| <a id="re-e005"></a>RE-E005 | `VersioningNotSupported` | 500 | A resource with `version_field` uses a repository without `supports_versions` (REST, SOAP). See [concurrency](concurrency.md). |
| <a id="re-e007"></a>RE-E007 | `TransactionDoomed` | 500 | An operation failed inside `manager.transaction()`; it can only roll back. `details` has the original `code` and `message`. See [lifecycle](lifecycle.md#grouping-operations). |
| <a id="re-e008"></a>RE-E008 | `IdempotencyNotSupported` | 500 | An idempotency key was sent but the unit of work has no idempotency store (`idempotency_table=`). See [idempotency](idempotency.md). |
| <a id="re-e009"></a>RE-E009 | `HistoryNotAvailable` | 500 | `history` was called for a resource without a state machine or with `audit = False`, or the manager has no audit trail stored as a resource (`audit=True`). See [audit](audit.md#history). |
| <a id="re-e101"></a>RE-E101 | `ValidationFailed` | 422 | Input does not match the schema; `details` holds Pydantic errors. |
| <a id="re-e102"></a>RE-E102 | `EntityNotFound` | 404 | No entity with that id. |
| <a id="re-e103"></a>RE-E103 | `InvalidQuery` | 400 | Filter/order field not allowed, or paging out of bounds. |
| <a id="re-e104"></a>RE-E104 | `ExternalEffectInTransaction` | 500 | An `@external_effect` hook is registered before `after_commit`. |
| <a id="re-e105"></a>RE-E105 | `VersionConflict` | 409 | The record changed since the client read it; `details.expected` and `details.current`. Reload and retry. |
| <a id="re-e106"></a>RE-E106 | `VersionRequired` | 428 | The resource has `require_version`; send `If-Match` (or `expected_version=`). |
| <a id="re-e107"></a>RE-E107 | `IdempotencyKeyReused` | 422 | The caller already used the idempotency key for a different request; `details.first` names it. Use a new key per request. |
| <a id="re-e108"></a>RE-E108 | `ReferenceNotFound` | 422 | A reference field points to a record that does not exist or that the caller may not read; `details` has `field`, `resource`, `id` ([relationships](relationships.md#integrity)). |
| <a id="re-e109"></a>RE-E109 | `ReferenceInUse` | 409 | The record is still referenced (`on_delete="restrict"`); `details` names the referencing `resource` and `field`. Change or delete those records first. |
| <a id="re-e201"></a>RE-E201 | `TransitionNotAllowed` | 409 | Transition not possible from the current state (or a guard failed); `details.allowed` lists valid ones. |
| <a id="re-e202"></a>RE-E202 | `UnknownTransition` | 404 | The resource has no such transition (or no state machine). |
| <a id="re-e301"></a>RE-E301 | `ActionNotFound` | 404 | The resource has no such action. |
| <a id="re-e401"></a>RE-E401 | `PublishFailed` | – | A broker did not accept some messages; `failed_ids` lists them. |
| <a id="re-e402"></a>RE-E402 | `NoHandler` | – | An inbound message has a type with no registered handler. |
| <a id="re-e403"></a>RE-E403 | `OutboxNotSupported` | 500 | `OutboxDelivery` used with a unit of work without `outbox()`. |
| <a id="re-e404"></a>RE-E404 | `InboxNotSupported` | – | A consumer's `inbox=` factory gives units of work without `inbox()`. |
| <a id="re-e405"></a>RE-E405 | `UndecodableMessage` | 422 (push) | An inbound body is neither an envelope nor a CloudEvent; the message is dead-lettered. |
| <a id="re-e406"></a>RE-E406 | `MessageRejected` | 422 (push) | Raised by a handler to dead-letter a message without retries. |
| <a id="re-e501"></a>RE-E501 | `RemoteCallFailed` | 502 | An external service (REST, SOAP) returned an error or could not be reached; `details` has `service`, `operation`, `status`. |
| <a id="re-e601"></a>RE-E601 | `DocumentSlotNotFound` | 404 | The resource has no document slot with that name. |
| <a id="re-e602"></a>RE-E602 | `ContentTypeNotAllowed` | 415 | The slot does not accept the file's content type. |
| <a id="re-e603"></a>RE-E603 | `DocumentTooLarge` | 413 | The file exceeds the slot's `max_bytes`. |
| <a id="re-e604"></a>RE-E604 | `UploadNotVerified` | 409 | A presigned upload is missing, too large or expired when confirmed. |
| <a id="re-e605"></a>RE-E605 | `DocumentNotAvailable` | 409 | The document is pending, processing, rejected or deleted. |
| <a id="re-e606"></a>RE-E606 | `RequiredDocumentMissing` | 409 | A transition needs an available document in a slot (`required_for`). |
| <a id="re-e607"></a>RE-E607 | `StorageFailed` | 502 | The blob store failed. |
| <a id="re-e608"></a>RE-E608 | `BlobNotFound` | 404 | No content stored under the key. |
| <a id="re-e701"></a>RE-E701 | `AuthenticationFailed` | 401 | Missing or invalid credentials; `details.reason` says why. |
| <a id="re-e702"></a>RE-E702 | `PermissionDenied` | 403 | The principal may not perform the action; `details.action` and `reason`. |
| <a id="re-e703"></a>RE-E703 | `InvalidPolicy` | 422 | A policy, role or assignment is not valid; `details.problems` lists JSON paths and messages. |
| <a id="re-e704"></a>RE-E704 | `PrivilegeEscalation` | 403 | An editor tried to grant actions they do not hold. |
| <a id="re-e705"></a>RE-E705 | `ConditionNotSupported` | 500 | A row-level filter is needed but cannot be applied (fails closed): the repository has no condition support, or an external policy engine's rule cannot be translated ([authorizers](authorizers.md)). |
| <a id="re-e706"></a>RE-E706 | `FieldNotWritable` | 403 | The input sets fields the principal may not write; `details.fields`. Nothing is saved. |
| <a id="re-e707"></a>RE-E707 | `FieldNotReadable` | 403 | A `list` filters or sorts by fields the principal may not read; `details.fields`. |
| <a id="re-e708"></a>RE-E708 | `TenantRequired` | 403 | A tenant-scoped resource needs a tenant: the caller has none, or a cross-tenant caller did not name one to create. |
| <a id="re-e709"></a>RE-E709 | `TenantMismatch` | 403 | The operation names a tenant other than the caller's. |

`DomainError` (business rule violations raised by your code) maps to HTTP 422 and
carries your own `code`.

## Tool rule codes

| Code | Tool | Meaning |
|---|---|---|
| RE-C001 | `check` | A `domain/` module imports an infrastructure package. |
| RE-C002 | `check` | Code inside the transaction (a hook before `after_commit`, an action's `execute`) reaches an external effect: an `@external_effect` function or a network, e-mail or cloud client. |
| RE-C003 | `check` | An `after_commit` hook records events, uses `ctx.repository()` / `ctx.uow`, registers `on_rollback`, or changes `ctx.entity`. |
| RE-C004 | `check` | An enabled operation that only a catch-all system role (`admin`) can perform: no other system role and no seed policy grants it. Needs `policies` in `[tool.resource-engine]`. |
| RE-C005 | `check` | The registry could not be loaded because the engine refused a declaration (for example RE-E004); reported at the line of your code. |
| RE-C006 | `check` | A hook or action records a domain event the resource does not declare in `events`; it is still published, but missing from the docs and the AsyncAPI document. |
| RE-D001…D009 | `docs validate` | Documentation coverage rules (resource, CRUD ops, state machine, states, transitions, actions, postconditions, hooks, document slots). |
| RE-D010 | `docs validate` | A description is still a `TODO` placeholder (left by `scaffold`); warning, fails with `--strict`. |
| RE-D011 | `docs validate` | A declared domain event (`Resource.events`) has no docstring; warning. |
