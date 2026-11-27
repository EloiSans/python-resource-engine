"""RE-C007: requested emails need their templates."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict
from resource_engine import OperationContext, Resource, ResourceRegistry
from resource_engine.email import EmailRequested
from resource_engine.templates import StringTemplates
from resource_engine_devtools.templates_check import check_templates


@dataclass
class Thing:
    name: str
    id: int | None = None


class ThingIn(BaseModel):
    name: str


class ThingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


def _request(ctx: OperationContext, template: str) -> None:
    ctx.record_event(EmailRequested(template=template, to=["a@x.com"]))


def welcome(ctx: OperationContext) -> None:
    """Welcomes."""
    ctx.record_event(EmailRequested(template="welcome", to=["a@x.com"], locale="ca"))
    ctx.record_event(EmailRequested(template="farewell", to=["a@x.com"]))
    ctx.record_event(EmailRequested(template="no_body", to=["a@x.com"]))
    _request(ctx, "computed")  # a computed name: not checked
    helper(ctx)


def helper(ctx: OperationContext) -> None:
    ctx.record_event(EmailRequested(template="from_helper", to=["a@x.com"]))


class ThingResource(Resource):
    """Things."""

    name = "things"
    model = Thing
    schemas = {"create": ThingIn, "read": ThingOut}
    operations = ["create"]
    hooks = {"after_create": [welcome]}


def test_missing_templates_are_reported():
    registry = ResourceRegistry()
    registry.register(ThingResource)
    templates = StringTemplates(
        {
            "ca/email/welcome.subject.txt": "Hola",
            "email/welcome.html": "<p>Hi</p>",  # fallback body for ca
            "email/no_body.subject.txt": "Subject only",
        }
    )
    found = check_templates(registry, templates)
    messages = sorted(v.message for v in found)
    assert {v.code for v in found} == {"RE-C007"}
    assert len(found) == 3
    assert "'farewell'" in messages[0] and "email/farewell.subject.txt" in messages[0]
    assert "'from_helper'" in messages[1]
    assert "'no_body'" in messages[2] and "email/no_body.txt or email/no_body.html" in messages[2]
