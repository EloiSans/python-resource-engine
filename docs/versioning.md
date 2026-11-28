# Versioning

Resources and events change. When a change would break clients or consumers
(a field renamed, a type changed), publish the new version next to the old
one, announce when the old one goes, and let the contract checks make sure
nothing else breaks.

## API versions

Serve each version under its own prefix. The last one is the current one:

```python
from resource_engine.versions import ApiVersion, Deprecation

mount_resources(app, registry, manager, versions=["v1", "v2"])
```

A resource whose schemas changed declares its older versions. Each converts
the older input to the current schema, and the current output to the older
shape. Everything else (validation, hooks, permissions, state machines,
events) is the same operation:

```python
class InvoiceResource(Resource):
    schemas = {"create": InvoiceIn, "read": InvoiceOut}  # v2: total + currency
    api_versions = {
        "v1": ApiVersion(
            read=InvoiceOutV1,  # v1: amount as text
            create=InvoiceInV1,
            to_read=lambda out: {"id": out["id"], "number": out["number"], "amount": out["total"]},
            from_create=lambda data: {"number": data["number"], "total": data["amount"]},
            deprecation=Deprecation(
                since=date(2026, 12, 1),
                sunset=date(2027, 6, 30),
                link="https://docs.example.com/invoices/v2",
            ),
        )
    }
```

- Resources without an entry for a version are served there unchanged.
- `to_read` gets the current output as JSON (fields hidden by permissions
  already left out), and its result is the response. Transitions and actions
  answer in the version's shape too.
- `from_create` / `from_update` get the input the client sent (only the
  fields it set).
- Without `versions`, routes have no version prefix, as before.

## Event versions

An event whose payload changes raises `schema_version` and keeps publishing
the older shapes for consumers that haven't moved:

```python
@dataclass(frozen=True, kw_only=True)
class InvoiceApproved(DomainEvent):
    schema_version = 2
    previous_versions = {
        1: EventVersion(
            InvoiceApprovedV1,  # the old payload type
            lambda p: {"invoice_id": p["invoice_id"], "amount": p["total"]},
            deprecation=Deprecation(sunset=date(2027, 6, 30)),
        )
    }
    invoice_id: int
    total: Decimal
```

- Each version is its own message, with the same type, key and correlation
  id. Its `schema_version` is in the envelope, in the broker attributes, and in
  CloudEvents `schemaversion`. Older versions get the id `<id>.v<n>`, so inbox
  de-duplication treats them as different messages.
- They go through the same outbox or delivery as the current version.
- Consumers pick the version they are written for:

  ```python
  @router.on(InvoiceApproved, payload=InvoiceApprovedV1, schema_version=1)
  async def on_approved(envelope, payload): ...
  ```

  Other versions of that type are acknowledged without calling the handler.
  Consumers on other stacks filter on the `schema_version` attribute (an SNS
  filter policy, a Pub/Sub filter, Service Bus rules).

## Deprecation notices

`Deprecation(since=, sunset=, link=, reason=)` marks an API version, a whole
resource (`Resource.deprecation`), an event or an event version. It shows up
everywhere clients look:

| Where | What |
|---|---|
| Responses | `Deprecation: @<since>` (RFC 9745), `Sunset: <date>` (RFC 8594), `Link: <link>; rel="deprecation"` |
| OpenAPI | `deprecated: true`, `x-deprecation` and the notice in the description |
| AsyncAPI | the payload schema `deprecated: true`, `x-deprecation` and the notice in the description |
| GraphQL | `@deprecated(reason: ...)` on a deprecated resource's queries and mutations |

A whole version can be deprecated at once:
`mount_resources(..., versions=["v1", "v2"], deprecated={"v1": Deprecation(...)})`.
Fields deprecated with pydantic (`Field(deprecated="Use number.")`) are
marked in OpenAPI and GraphQL as well.

## Contract checks

Publish the documents and check every change against them in CI:

```bash
resource-engine docs asyncapi --check docs/asyncapi.json
resource-engine docs openapi --check docs/openapi.json
```

- **Events:** each event version is compared with the same version of the
  published document. A new version may change anything. Removing a version
  is breaking until its sunset has passed.
- **REST:** removed operations (unless deprecated and past their sunset),
  response fields removed or changed, request fields or parameters newly
  required. `docs openapi` reads the app from `[tool.resource-engine] app`.

The usual way to retire a version:

1. Publish the new version next to the old one.
2. Deprecate the old one with a sunset.
3. Remove it after the sunset; the checks then let it go.

See [contracts](contracts.md) for the consumer side.
