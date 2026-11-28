"""Deprecations in the GraphQL schema: a deprecated resource's root fields and
deprecated read fields carry @deprecated."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from pydantic import BaseModel, ConfigDict, Field
from resource_engine import Resource, ResourceManager, ResourceRegistry
from resource_engine.testing import InMemoryStore
from resource_engine.versions import Deprecation
from resource_engine_graphql import GraphQLAdapter


@dataclass
class Fax:
    number: str
    code: str = ""
    id: int | None = None


class FaxIn(BaseModel):
    number: str


class FaxOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    number: str
    code: str = Field("", deprecated="Use number.")


class FaxResource(Resource):
    model = Fax
    schemas = {"create": FaxIn, "read": FaxOut}
    operations = ["list", "get", "create"]
    deprecation = Deprecation(sunset=date(2027, 6, 30), reason="Send emails instead.")


def test_deprecations_reach_the_schema():
    registry = ResourceRegistry()
    registry.register(FaxResource)
    manager = ResourceManager(registry, InMemoryStore.from_registry(registry).unit_of_work)
    schema = GraphQLAdapter(registry, manager).schema
    query = schema.query_type.fields
    notice = "Deprecated; removed on 2027-06-30. Send emails instead."
    assert {f.deprecation_reason for f in query.values()} == {notice}
    assert {f.deprecation_reason for f in schema.mutation_type.fields.values()} == {notice}
    fax = schema.get_type("Fax").fields
    assert fax["code"].deprecation_reason == "Use number."
    assert fax["number"].deprecation_reason is None
