from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import BaseModel
from resource_engine import Resource, ResourceRegistry, external_effect
from resource_engine.errors import (
    ConfigurationError,
    ExternalEffectInTransaction,
    ResourceNotRegistered,
    StateFieldExposed,
)
from statemachine import State, StateMachine


@dataclass
class Thing:
    title: str
    status: str | None = None
    id: int | None = None


class ThingIn(BaseModel):
    title: str


class ThingOut(BaseModel):
    id: int
    title: str


class Flow(StateMachine):
    open = State(initial=True)
    closed = State(final=True)
    close = open.to(closed)


def test_default_name_and_registration():
    class ThingResource(Resource):
        model = Thing
        schemas = {"create": ThingIn, "read": ThingOut}
        operations = ["create", "get"]

    registry = ResourceRegistry()
    registry.register(ThingResource)
    assert "things" in registry
    assert registry.get(ThingResource).name == "things"


def test_missing_schema_is_reported():
    class BadResource(Resource):
        model = Thing
        operations = ["create"]

    with pytest.raises(ConfigurationError) as info:
        BadResource.definition()
    assert info.value.code == "RE-E001"
    assert "create" in info.value.message


def test_unknown_operation_points_to_actions():
    class BadResource(Resource):
        model = Thing
        operations = ["approve"]

    with pytest.raises(ConfigurationError, match="unknown operations"):
        BadResource.definition()


def test_state_field_cannot_be_in_write_schemas():
    class ThingWithStatus(BaseModel):
        title: str
        status: str

    class BadResource(Resource):
        model = Thing
        schemas = {"create": ThingWithStatus, "read": ThingOut}
        operations = ["create"]
        state_machine = Flow

    with pytest.raises(StateFieldExposed) as info:
        BadResource.definition()
    assert info.value.code == "RE-E004"


def test_unknown_hook_point():
    class BadResource(Resource):
        model = Thing
        hooks = {"before_save": [lambda ctx: None]}

    with pytest.raises(ConfigurationError, match="Unknown hook point"):
        BadResource.definition()


def test_external_effect_hook_must_run_after_commit():
    @external_effect
    def send_email(ctx):
        pass

    class BadResource(Resource):
        model = Thing
        hooks = {"before_create": [send_email]}

    with pytest.raises(ExternalEffectInTransaction) as info:
        BadResource.definition()
    assert info.value.code == "RE-E104"
    assert "after_commit" in str(info.value)


def test_qualified_hook_must_reference_existing_transition():
    class BadResource(Resource):
        model = Thing
        state_machine = Flow
        hooks = {"before_transition:reopen": [lambda ctx: None]}

    with pytest.raises(ConfigurationError, match="unknown 'reopen'"):
        BadResource.definition()


def test_unregistered_resource():
    with pytest.raises(ResourceNotRegistered):
        ResourceRegistry().get("nope")
