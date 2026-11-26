# resource-engine-graphql

GraphQL adapter for [Resource Engine](https://github.com/EloiSans/python-resource-engine).
It generates a schema from the registry and calls the `ResourceManager` from
every resolver.

```python
from resource_engine_graphql import mount_graphql

mount_graphql(app, registry, manager, actor_dependency=bearer_actor(oidc))
# POST /graphql, GET /graphql?query=..., GET /graphql/schema.graphql
```

- **Schema:**
  - camelCase fields, one type per resource;
  - queries with offset pages and typed filters;
  - mutations for create, update, delete, transitions and actions.
- **Relations:** relation fields (references, reverse relations,
  many-to-many) are batched per request.
- **Errors:** engine errors carry their `RE-*` code in `extensions`.
- **Protection:** a depth limit.
- **Permissions:** the caller's permissions and audit apply as over REST.

See [docs/graphql.md](https://github.com/EloiSans/python-resource-engine/blob/main/docs/graphql.md).
