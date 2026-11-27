"""OPA bundles with the catalogue: contents, manifest, reproducible bytes."""

from __future__ import annotations

import io
import json
import tarfile

from invoices.bootstrap import authz, registry
from resource_engine_devtools.cli import main
from resource_engine_opa import catalog_data, opa_bundle

POLICY = """package resource_engine

import rego.v1

actions := data.resource_engine.catalog.actions

allow if {
	actions[input.action].resource == "invoices"
	"clerk" in input.subject.roles
}
"""


def _files(bundle: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:gz") as archive:
        return {
            m.name.lstrip("/"): archive.extractfile(m).read()  # type: ignore[union-attr]
            for m in archive.getmembers()
        }


def test_catalogue_data():
    data = catalog_data(registry, authz)
    approve = data["actions"]["invoices:transition:approve"]
    assert approve == {"resource": "invoices", "kind": "transition", "qualifier": "approve",
                       "restricted": False, "input": {}}  # fmt: skip
    assert data["actions"]["invoices:read:notes"]["restricted"] is True
    invoices = data["resources"]["invoices"]
    assert invoices["states"]["status"][:2] == ["draft", "pending"]
    assert invoices["restricted_fields"] == ["notes"] and "amount" in invoices["attributes"]
    assert "admin" in data["system_roles"]


def test_bundle_contents_and_reproducibility(tmp_path):
    (tmp_path / "authz.rego").write_text(POLICY)
    (tmp_path / "authz_test.rego").write_text("package resource_engine\n")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "more.rego").write_text("package resource_engine.more\n")
    bundle = opa_bundle(registry, authz, policies=tmp_path)
    files = _files(bundle)
    assert set(files) == {
        ".manifest",
        "resource_engine/catalog/data.json",
        "authz.rego",
        "nested/more.rego",
    }  # tests stay out
    manifest = json.loads(files[".manifest"])
    assert manifest["roots"] == ["resource_engine"] and len(manifest["revision"]) == 16
    assert opa_bundle(registry, authz, policies=tmp_path) == bundle  # same input, same bytes
    (tmp_path / "authz.rego").write_text(POLICY + "\n# changed\n")
    changed = json.loads(_files(opa_bundle(registry, authz, policies=tmp_path))[".manifest"])
    assert changed["revision"] != manifest["revision"]


def test_cli(tmp_path):
    (tmp_path / "authz.rego").write_text(POLICY)
    out = tmp_path / "bundle.tar.gz"
    args = ["authz", "opa-bundle", "--policies", str(tmp_path / "authz.rego"), "--out", str(out)]
    assert main(args) == 0
    assert "authz.rego" in _files(out.read_bytes())
