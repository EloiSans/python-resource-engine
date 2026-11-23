# resource-engine-opa

[Open Policy Agent](https://www.openpolicyagent.org/) for Resource Engine. Each
check is a call to OPA's data API; when the record is not loaded yet (step 2,
`list` row filters, field rules) the engine asks the compile API instead and
turns its UCAST answer into the engine's conditions (SQL with SQLAlchemy).

```python
from resource_engine.authz import EngineAuthorizer
from resource_engine_opa import OpaEngine

authorizer = EngineAuthorizer(OpaEngine("http://opa:8181"), config)
```

```rego
package resource_engine

import rego.v1

allow if {
	input.action == "invoices:get"
	input.resource.owner == input.subject.id
}
```

Tested against `openpolicyagent/opa:1.22.4` (see
`compose.emulators.yaml` and `docker/opa/`). See
[docs/authorizers.md](../../docs/authorizers.md#opa). Part of
[Resource Engine](../../README.md).
