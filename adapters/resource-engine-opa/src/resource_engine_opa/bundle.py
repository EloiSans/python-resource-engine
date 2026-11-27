"""OPA bundles with the application's catalogue: its resources, actions,
attributes and system roles as data, next to your Rego policies.

```bash
uv run resource-engine authz opa-bundle --policies policies/ --out bundle.tar.gz
```

OPA downloads bundles from any HTTP server (S3, a CDN, nginx, GitHub Pages):
build it in CI and publish it where OPA polls. Policies can then look the
catalogue up instead of repeating it:

```rego
package resource_engine

import rego.v1

actions := data.resource_engine.catalog.actions

allow if {
        catalog.actions[input.action].kind in {"crud", "transition"}
        catalog.actions[input.action].resource == "invoices"
        "clerk" in input.subject.roles
}
```

The bundle is ``.manifest`` (roots ``resource_engine``, a revision that is the
content's hash), ``resource_engine/catalog/data.json`` and every ``.rego``
file under ``policies`` at its relative path. The same input gives the same
bytes, so the revision only changes with the content.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path
from typing import Any

ROOT = "resource_engine"


def catalog_data(registry: Any, authz: Any = None) -> dict[str, Any]:
    """The catalogue as JSON data for policies."""
    from resource_engine.authz.catalog import Catalog

    catalog = Catalog.from_registry(registry)
    actions = {
        name: {
            "resource": info.resource,
            "kind": info.kind,
            "qualifier": info.qualifier,
            "restricted": info.restricted,
            "input": {a.path.removeprefix("input."): a.type for a in info.input},
        }
        for name, info in sorted(catalog.actions.items())
    }
    resources = {}
    for definition in registry:
        attributes = catalog.resource_attributes.get(definition.name, [])
        resources[definition.name] = {
            "id_field": definition.id_field,
            "tenant_field": definition.tenant_field,
            "restricted_fields": sorted(definition.restricted_fields),
            "attributes": {
                a.path.removeprefix("resource."): {"type": a.type, "enum": a.enum}
                for a in attributes
            },
            "states": {
                machine.state_field: [s.id for s in machine.info.states]
                for machine in definition.machines.values()
            },
        }
    roles = {
        role.name: {"grants": list(role.grants), "description": role.description}
        for role in getattr(authz, "system_roles", ()) or ()
    }
    return {"actions": actions, "resources": resources, "system_roles": roles}


def opa_bundle(
    registry: Any,
    authz: Any = None,
    *,
    policies: str | Path | None = None,
    root: str = ROOT,
) -> bytes:
    """The bundle (``tar.gz``) as bytes: the catalogue under ``<root>/catalog``
    and the ``.rego`` files under ``policies``."""
    files: dict[str, bytes] = {
        f"{root}/catalog/data.json": json.dumps(
            catalog_data(registry, authz), indent=2, sort_keys=True, default=str
        ).encode()
    }
    if policies is not None:
        base = Path(policies)
        sources = sorted(base.rglob("*.rego")) if base.is_dir() else [base]
        for path in sources:
            if path.name.endswith("_test.rego"):
                continue  # tests stay in the repository
            relative = path.relative_to(base) if base.is_dir() else Path(path.name)
            files[relative.as_posix()] = path.read_bytes()
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode() + b"\0" + files[name] + b"\0")
    manifest = {"revision": digest.hexdigest()[:16], "roots": [root]}
    files[".manifest"] = json.dumps(manifest, sort_keys=True).encode()

    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name in sorted(files):
            info = tarfile.TarInfo(f"/{name}")
            info.size = len(files[name])
            info.mtime = 0  # reproducible: same content, same bytes
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(files[name]))
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", mtime=0) as gz:
        gz.write(raw.getvalue())
    return out.getvalue()


__all__ = ["ROOT", "catalog_data", "opa_bundle"]
