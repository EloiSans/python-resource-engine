# Polymorphic resources

Some records come in kinds that share most of their data: requests can be
vacation requests or expense claims, and each kind adds fields of its own. A
**base resource** holds what they share and lists every kind together. Each
**subtype** adds its fields and its own create, update and delete.

```python
class RequestResource(Resource):
    """Every request, whatever its kind."""

    name = "requests"
    model = Request  # common fields
    schemas = {"read": RequestRead}
    operations = ["list", "get"]  # a base only lists and reads
    discriminator = "type"  # each record's subtype
    state_machine = RequestFlow  # shared: runs on every kind
    version_field = "version"  # shared too: ids, tenancy, versions
    hooks = {"before_create": [check_title]}


class VacationResource(Resource):
    """Vacation requests."""

    name = "vacation_requests"
    extends = RequestResource
    identity = "vacation"  # the discriminator value (default: its name)
    model = Vacation  # subclass of Request
    schemas = {"create": VacationIn, "update": VacationIn, "read": VacationRead}
    operations = ["list", "get", "create", "update", "delete"]
```

`registry.register(VacationResource)` registers the base too.

## What the base does

- **One family of records.** Every subtype record is a base record: one id
  space, and `Reference("requests")` points at a request of any kind.
- **Reads of every kind.** `GET /requests` lists them all (filter by kind with
  `?type=vacation`), and each item comes back with its subtype's read schema,
  so a vacation request includes `days`. The base filters, sorts and row
  conditions apply.
- **Shared behaviour.** The base's state machines, timers and actions run on
  every kind, at the base's routes: `POST /requests/7/transitions/grant`.
  Subtypes have no machines of their own. Their records get the base's
  initial states on create.
- **Shared hooks.** The base's unqualified create, update, delete, validation
  and after-commit hooks run for every subtype, before the subtype's own
  hooks. Transition and action hooks stay on the base, where those run.
- **No writes.** `create`, `update` and `delete` belong to the subtypes, each
  with its own schemas. The engine sets the discriminator on create.

## What a subtype does

- Its own CRUD routes, schemas, hooks, documents, references, actions and
  restricted fields: `/vacation_requests`, `GET /vacation_requests/7` (a 404
  for a record of another kind).
- Its model subclasses the base model. Its read schema includes every base
  read field: subclass the base's read schema.
- Ids, the discriminator, the state fields and the version field are the
  engine's: they can't be in its create or update schemas.
- `tenant_field`, `version_field` and `require_version` come from the base.
  Only one level of subtypes is supported.

## Permissions

- **Reads through the base cover every kind.** `requests:list`, `requests:get`
  and the base's transitions and actions apply to records of any kind, and the
  base's row conditions too. A subtype's restricted fields stay hidden when
  read through the base: read them through the subtype, with its grants.
- **Writes need the subtype's grant**: `vacation_requests:create`,
  `vacation_requests:update`. Reads through the subtype's own routes need its
  grants too.

## Storage

SQL uses a base table with the common columns, the ids and the
discriminator, plus one extension table per subtype whose primary key is also
a foreign key to the base row (joined-table inheritance):

```python
from resource_engine_sqlalchemy import extension_table, map_family

requests = Table(
    "requests",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("type", String(32), nullable=False, index=True),
    Column("title", String(200), nullable=False),
    Column("status", String(32), nullable=False),
    Column("version", Integer, nullable=False),
)
vacation_requests = extension_table(
    "vacation_requests", requests, Column("days", Integer, nullable=False)
)

map_family(
    mapper_registry,
    registry,
    "requests",
    {"requests": requests, "vacation_requests": vacation_requests},
)
```

`map_family` maps the base with the discriminator and each registered subtype
on its table. Reads of the base load every subtype's columns in the same
query, one outer join per subtype. Version checks and deletes cover both
rows. `InMemoryStore.from_registry` keeps subtype records in the base's table.

## The generated surfaces

- **OpenAPI:** base responses are `anyOf` the subtypes' read schemas.
- **GraphQL:** the base is an interface (`interface Request`) and each subtype
  a type implementing it (`type VacationRequest implements Request`), with the
  base's relation and transition fields. Use `__typename` and inline fragments:
  `requests { items { id ... on VacationRequest { days } } }`.
- **MCP:** the base's list, get, transition and action tools, plus each
  subtype's own tools.
- **Docs:** the base page lists its subtypes, and each subtype links to its base.

## When not to use it

- Kinds that share a name but little data: separate resources.
- Read-only aggregates across unrelated tables: view resources (on the
  [roadmap](roadmap.md)).
