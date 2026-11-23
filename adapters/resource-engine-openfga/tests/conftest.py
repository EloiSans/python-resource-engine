"""OpenFGA fixtures (see compose.emulators.yaml): one store and model per test."""

from __future__ import annotations

import os
import urllib.request
import uuid

import httpx
import pytest

OPENFGA_URL = os.getenv("OPENFGA_URL", "http://127.0.0.1:8080")


def _up() -> bool:
    try:
        with urllib.request.urlopen(f"{OPENFGA_URL}/healthz", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def pytest_collection_modifyitems(config, items):
    selected = [
        i for i in items if (m := i.get_closest_marker("emulator")) and m.args[:1] == ("openfga",)
    ]
    if not selected or _up():
        return
    skip = pytest.mark.skip(reason=f"OpenFGA not reachable on {OPENFGA_URL}")
    for item in selected:
        item.add_marker(skip)


def direct(*types: str) -> dict:
    return {"directly_related_user_types": [_user_type(t) for t in types]}


def _user_type(spec: str) -> dict:
    kind, _, relation = spec.partition("#")
    return {"type": kind, "relation": relation} if relation else {"type": kind}


def model(resource: str) -> dict:
    """``owner``/``creator``/``auditor`` on ``resource``; auditors and creators
    through ``role#member`` on the collection object."""
    this = {"this": {}}
    from_collection = {
        "tupleToUserset": {
            "tupleset": {"object": "", "relation": "collection"},
            "computedUserset": {"object": "", "relation": "auditor"},
        }
    }
    return {
        "schema_version": "1.1",
        "type_definitions": [
            {"type": "user"},
            {
                "type": "role",
                "relations": {"member": this},
                "metadata": {"relations": {"member": direct("user")}},
            },
            {
                "type": resource,
                "relations": {
                    "collection": this,
                    "owner": this,
                    "creator": this,
                    "auditor": {"union": {"child": [this, from_collection]}},
                },
                "metadata": {
                    "relations": {
                        "collection": direct(resource),
                        "owner": direct("user"),
                        "creator": direct("role#member"),
                        "auditor": direct("role#member"),
                    }
                },
            },
        ],
    }


@pytest.fixture
async def fga_store():
    """``(store_id, model_id)`` of a fresh store with the test model."""

    async def setup(resource: str) -> tuple[str, str]:
        async with httpx.AsyncClient(base_url=OPENFGA_URL) as http:
            store = (await http.post("/stores", json={"name": f"t-{uuid.uuid4().hex}"})).json()
            r = await http.post(f"/stores/{store['id']}/authorization-models", json=model(resource))
            r.raise_for_status()
            return store["id"], r.json()["authorization_model_id"]

    return setup


@pytest.fixture
async def fga_engine(fga_store):
    from resource_engine_openfga import OpenFgaEngine

    engines = []

    async def make(resource: str, relations: dict[str, str]) -> OpenFgaEngine:
        store_id, model_id = await fga_store(resource)
        engine = OpenFgaEngine(
            api_url=OPENFGA_URL, store_id=store_id, model_id=model_id, relations=relations
        )
        from openfga_sdk.client.models import ClientTuple

        await engine.client.write_tuples(
            [
                ClientTuple(
                    user="role:writer#member", relation="creator", object=f"{resource}:collection"
                ),
                ClientTuple(
                    user="role:auditor#member", relation="auditor", object=f"{resource}:collection"
                ),
            ]
        )
        engines.append(engine)
        return engine

    yield make
    for engine in engines:
        await engine.client.close()
