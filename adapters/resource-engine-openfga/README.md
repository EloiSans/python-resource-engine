# resource-engine-openfga

[OpenFGA](https://openfga.dev/) relationship-based authorization for Resource
Engine. Actions map to relations; records map to objects (`invoices:42`). Row
filters for `list` come from `list_objects` (`id in [...]`), and `TupleSync`
writes the tuples a record implies when it is created.

```python
from resource_engine.authz import EngineAuthorizer
from resource_engine_openfga import OpenFgaEngine, TupleSync

engine = OpenFgaEngine(
    api_url="http://openfga:8080",
    store_id=STORE_ID,
    relations={"invoices:get": "viewer", "invoices:list": "viewer", "invoices:create": "creator"},
)
authorizer = EngineAuthorizer(engine, config)
tuples = TupleSync(engine, {"invoices": lambda i: [(engine.user_ref(i.owner), "owner")]})
```

Tested against `openfga/openfga:v1.22.0` (see `compose.emulators.yaml`). See
[docs/authorizers.md](../../docs/authorizers.md#openfga). Part of
[Resource Engine](../../README.md).
