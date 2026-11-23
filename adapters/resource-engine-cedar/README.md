# resource-engine-cedar

[Cedar](https://www.cedarpolicy.com/) policies for Resource Engine, evaluated in
process with `cedarpy`. Row filters for `list` and field rules come from Cedar's
partial evaluation, translated into the engine's conditions (SQL with
SQLAlchemy).

```python
from pathlib import Path

from resource_engine.authz import EngineAuthorizer
from resource_engine_cedar import CedarEngine

authorizer = EngineAuthorizer(CedarEngine(Path("policies/")), config)
manager = ResourceManager(registry, uow_factory, authorizer=authorizer)
```

```cedar
permit(principal, action == Action::"invoices:get", resource is Invoices)
when { resource.owner == principal.id };
```

See [docs/authorizers.md](../../docs/authorizers.md#cedar). Part of
[Resource Engine](../../README.md).
