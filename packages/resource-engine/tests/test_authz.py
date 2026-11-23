from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest
from pydantic import BaseModel, ConfigDict
from resource_engine import DocumentSlot, ListQuery, Resource, ResourceManager, ResourceRegistry
from resource_engine.authz import (
    UNKNOWN,
    AuthzConfig,
    CombinedRoleSource,
    Env,
    IdpRoleSource,
    PolicyAuthorizer,
    StoredRoleSource,
    SystemRole,
    action_matches,
    evaluate,
    partial,
    system_principal,
    validate,
)
from resource_engine.errors import (
    ConditionNotSupported,
    ConfigurationError,
    EntityNotFound,
    FieldNotReadable,
    FieldNotWritable,
    InvalidPolicy,
    PermissionDenied,
    PrivilegeEscalation,
)
from resource_engine.ports.identity import Principal
from resource_engine.testing import InMemoryBlobStore, InMemoryStore
from statemachine import State, StateMachine

# ------------------------------------------------------------------ language


def test_condition_validation_reports_paths():
    types = {"resource.amount": "number", "resource.tenant": "string", "subject.tenant": "string"}
    assert validate({"lt": [{"attr": "resource.amount"}, 10]}, types) == []
    problems = validate(
        {
            "and": [
                {"eq": [{"attr": "resource.amout"}, 1]},
                {"lt": [{"attr": "resource.tenant"}, True]},
                {"in": [{"attr": "resource.tenant"}, "x"]},
                {"bogus": []},
            ]
        },
        types,
    )
    assert [p.path for p in problems] == [
        "$.and[0].eq[0]",
        "$.and[1].lt",
        "$.and[2].in[1]",
        "$.and[3]",
    ]


def test_three_valued_and_partial_evaluation():
    cond = {
        "and": [
            {"eq": [{"attr": "resource.tenant"}, {"attr": "subject.tenant"}]},
            {"contains": [{"attr": "subject.roles"}, "approver"]},
        ]
    }
    known = Env(subject={"tenant": "t1", "roles": ["approver"]})
    assert evaluate(cond, known) is UNKNOWN
    assert partial(cond, known) == {"eq": [{"attr": "resource.tenant"}, "t1"]}
    assert evaluate(cond, Env(subject={"tenant": "t1", "roles": []})) is False
    full = Env(subject={"tenant": "t1", "roles": ["approver"]}, resource={"tenant": "t1"})
    assert evaluate(cond, full) is True
    assert (
        evaluate(
            {"lt": [{"attr": "resource.amount"}, 10000]},
            Env(resource={"amount": Decimal("9999.99")}),
        )
        is True
    )


def test_action_patterns_and_sensitive_resources():
    assert action_matches("*:list", "invoices:list")
    assert action_matches("invoices:*", "invoices:transition:approve")
    assert action_matches("*:download:*", "invoices:download:signed_pdf")
    assert not action_matches("*:list", "invoices:transition:approve")
    assert not action_matches("*:list", "authz_policies:list", wildcards=False)
    assert action_matches("authz_policies:list", "authz_policies:list", wildcards=False)


# ------------------------------------------------------------------ engine


@dataclass
class Invoice:
    tenant: str
    amount: Decimal
    status: str | None = None
    id: int | None = None


class InvoiceMachine(StateMachine):
    draft = State(initial=True)
    approved = State(final=True)
    approve = draft.to(approved)


class InvoiceIn(BaseModel):
    tenant: str
    amount: Decimal


class InvoiceOut(InvoiceIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    status: str


class InvoiceResource(Resource):
    model = Invoice
    schemas = {"create": InvoiceIn, "update": InvoiceIn, "read": InvoiceOut}
    operations = ["list", "get", "create", "update", "delete"]
    state_machine = InvoiceMachine
    filterable = ["tenant"]
    sortable = ["id", "amount"]
    documents = [DocumentSlot("pdf", content_types=["application/pdf"])]


def principal(name: str, *roles: str, tenant: str | None = None) -> Principal:
    return Principal(subject=name, issuer="https://idp", roles=frozenset(roles), tenant=tenant)


ADMIN = principal("root", "platform-admin")
VIEWER = principal("vera", "viewers")
ANNA = principal("anna", "approvers", tenant="t1")
BORIS = principal("boris", "approvers", tenant="t2")


@pytest.fixture
async def env():
    registry = ResourceRegistry()
    registry.register(InvoiceResource)
    registry.register_documents()
    registry.register_authz()
    store = InMemoryStore.from_registry(registry)
    stored = StoredRoleSource(store.unit_of_work)
    config = AuthzConfig(
        system_roles=[SystemRole.admin(), SystemRole.viewer()],
        role_source=CombinedRoleSource(
            IdpRoleSource({"viewers": "viewer", "approvers": "approver"}), stored
        ),
        bootstrap={"platform-admin": "admin"},
    )
    authz = PolicyAuthorizer(config, store.unit_of_work)
    manager = ResourceManager(
        registry, store.unit_of_work, authorizer=authz, blob_store=InMemoryBlobStore()
    )
    system = system_principal()
    for tenant, amount in [("t1", 100), ("t1", 50_000), ("t2", 200)]:
        await manager.create("invoices", {"tenant": tenant, "amount": amount}, actor=system)
    await manager.create("authz_roles", {"name": "approver"}, actor=ADMIN)
    await manager.create(
        "authz_policies",
        {
            "name": "Approvers see their tenant's invoices",
            "effect": "allow",
            "subjects": {"roles": ["approver"]},
            "actions": ["invoices:list", "invoices:get"],
            "condition": {"eq": [{"attr": "resource.tenant"}, {"attr": "subject.tenant"}]},
        },
        actor=ADMIN,
    )
    await manager.create(
        "authz_policies",
        {
            "name": "Approvers approve up to 10k",
            "effect": "allow",
            "subjects": {"roles": ["approver"]},
            "actions": ["invoices:transition:approve"],
            "condition": {
                "and": [
                    {"eq": [{"attr": "resource.tenant"}, {"attr": "subject.tenant"}]},
                    {"lt": [{"attr": "resource.amount"}, 10000]},
                ]
            },
        },
        actor=ADMIN,
    )
    return manager, authz


async def test_viewer_system_role_is_read_only(env):
    manager, _ = env
    page = await manager.list("invoices", actor=VIEWER)
    assert page.total == 3
    assert (await manager.get("invoices", 1, actor=VIEWER)).id == 1
    with pytest.raises(PermissionDenied):
        await manager.create("invoices", {"tenant": "t1", "amount": 1}, actor=VIEWER)
    with pytest.raises(PermissionDenied):  # sensitive: wildcards do not reach it
        await manager.list("authz_policies", actor=VIEWER)


async def test_row_level_visibility_and_conditional_transition(env):
    manager, _ = env
    page = await manager.list("invoices", ListQuery(order_by=["id"]), actor=ANNA)
    assert [i.id for i in page.items] == [1, 2] and page.total == 2
    assert (await manager.list("invoices", actor=BORIS)).total == 1
    with pytest.raises(EntityNotFound):  # another tenant's record is invisible
        await manager.get("invoices", 3, actor=ANNA)
    assert await manager.allowed_transitions("invoices", 1, actor=ANNA) == ["approve"]
    assert await manager.allowed_transitions("invoices", 2, actor=ANNA) == []
    with pytest.raises(PermissionDenied):  # 50k is above the limit
        await manager.transition("invoices", 2, "approve", actor=ANNA)
    assert (await manager.transition("invoices", 1, "approve", actor=ANNA)).status == "approved"
    with pytest.raises(PermissionDenied):
        await manager.update("invoices", 1, {"tenant": "t1", "amount": 1}, actor=ANNA)


async def test_deny_overrides_allow_but_not_system_roles(env):
    manager, _ = env
    await manager.create(
        "authz_policies",
        {
            "name": "Nobody lists invoices",
            "effect": "deny",
            "subjects": {"everyone": True},
            "actions": ["invoices:list"],
        },
        actor=ADMIN,
    )
    with pytest.raises(PermissionDenied):
        await manager.list("invoices", actor=ANNA)
    assert (await manager.list("invoices", actor=VIEWER)).total == 3  # system role wins
    with pytest.raises(InvalidPolicy) as info:
        await manager.create(
            "authz_policies",
            {
                "name": "Restrict viewers",
                "effect": "deny",
                "subjects": {"roles": ["viewer"]},
                "actions": ["invoices:get"],
            },
            actor=ADMIN,
        )
    assert info.value.details["problems"][0]["path"] == "$.subjects.roles[0]"


async def test_invalid_conditions_are_reported_to_the_editor(env):
    manager, _ = env
    with pytest.raises(InvalidPolicy) as info:
        await manager.create(
            "authz_policies",
            {
                "name": "Typo",
                "effect": "allow",
                "subjects": {"roles": ["approver"]},
                "actions": ["invoices:update", "nothing:here"],
                "condition": {"gt": [{"attr": "resource.amout"}, 5]},
            },
            actor=ADMIN,
        )
    paths = [p["path"] for p in info.value.details["problems"]]
    assert paths == ["$.actions[1]", "$.condition.gt[0]"]


async def test_no_privilege_escalation(env):
    manager, _ = env
    editor = principal("eddie", "editors")
    await manager.create("authz_roles", {"name": "editor"}, actor=ADMIN)
    await manager.create(
        "authz_role_assignments", {"role": "editor", "principal": editor.id}, actor=ADMIN
    )
    await manager.create(
        "authz_policies",
        {
            "name": "Editors manage policies and list invoices",
            "effect": "allow",
            "subjects": {"roles": ["editor"]},
            "actions": ["authz_policies:create", "authz_role_assignments:create", "invoices:list"],
        },
        actor=ADMIN,
    )
    grant_list = {
        "name": "ok",
        "effect": "allow",
        "subjects": {"roles": ["approver"]},
        "actions": ["invoices:list"],
    }
    await manager.create("authz_policies", grant_list, actor=editor)
    with pytest.raises(PrivilegeEscalation):
        await manager.create(
            "authz_policies", {**grant_list, "actions": ["invoices:delete"]}, actor=editor
        )
    with pytest.raises(PrivilegeEscalation):  # nor can they hand out admin
        await manager.create(
            "authz_role_assignments", {"role": "admin", "principal": editor.id}, actor=editor
        )


async def test_stored_roles_and_immediate_policy_changes(env):
    manager, authz = env
    carla = principal("carla")  # no provider roles
    with pytest.raises(PermissionDenied):
        await manager.list("invoices", actor=carla)
    await manager.create(
        "authz_role_assignments", {"role": "viewer", "principal": carla.id}, actor=ADMIN
    )
    assert (await manager.list("invoices", actor=carla)).total == 3


async def test_anonymous_and_system_principal(env):
    manager, _ = env
    with pytest.raises(PermissionDenied):
        await manager.list("invoices")
    assert (await manager.list("invoices", actor=system_principal())).total == 3


async def test_document_gates(env):
    manager, _ = env
    with pytest.raises(PermissionDenied):
        await manager.documents.attach(
            "invoices",
            1,
            "pdf",
            b"%PDF",
            filename="a.pdf",
            content_type="application/pdf",
            actor=VIEWER,
        )
    doc = await manager.documents.attach(
        "invoices",
        1,
        "pdf",
        b"%PDF",
        filename="a.pdf",
        content_type="application/pdf",
        actor=ADMIN,
    )
    _, stream = await manager.documents.open(doc.id, actor=VIEWER)
    assert b"".join([c async for c in stream]) == b"%PDF"
    with pytest.raises(PermissionDenied):
        await manager.documents.open(doc.id, actor=ANNA)


async def test_permissions_for_the_front_end(env):
    _, authz = env
    perms = await authz.permissions(ANNA)
    assert perms["invoices:list"] == "conditional"
    assert perms["invoices:transition:approve"] == "conditional"
    assert "invoices:create" not in perms
    assert (await authz.permissions(VIEWER))["invoices:download:pdf"] == "allowed"
    catalog = authz.catalog.to_dict()
    states = next(
        a for a in catalog["resources"]["invoices"]["attributes"] if a["path"] == "resource.status"
    )
    assert states["enum"] == ["draft", "approved"]


async def test_repositories_without_conditions_fail_closed(env):
    manager, _ = env

    class Plain:
        def __init__(self, inner):
            self.inner = inner

        async def get(self, entity_id):
            return await self.inner.get(entity_id)

        async def list(self, query):
            return await self.inner.list(query)

    original = manager.uow_factory

    class Uow:
        async def __aenter__(self):
            self.u = await original().__aenter__()
            return self

        async def __aexit__(self, *exc):
            return None

        def repository(self, name):
            return Plain(self.u.repository(name))

    manager.uow_factory = Uow
    try:
        with pytest.raises(ConditionNotSupported):
            await manager.list("invoices", actor=ANNA)
    finally:
        manager.uow_factory = original


# ------------------------------------------------------------------ fields


@dataclass
class Employee:
    name: str
    team: str
    salary: int
    notes: str | None = None
    id: int | None = None


class EmployeeIn(BaseModel):
    name: str
    team: str
    salary: int
    notes: str | None = None


class EmployeePatch(BaseModel):
    name: str | None = None
    team: str | None = None
    salary: int | None = None
    notes: str | None = None


class EmployeeOut(EmployeeIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class EmployeeResource(Resource):
    model = Employee
    schemas = {"create": EmployeeIn, "update": EmployeePatch, "read": EmployeeOut}
    operations = ["list", "get", "create", "update"]
    filterable = ["team", "salary"]
    sortable = ["id", "salary"]
    restricted_fields = ["salary"]


HR = principal("helen", "hr")
STAFF = principal("sam", "staff")


@pytest.fixture
async def fields_env():
    registry = ResourceRegistry()
    registry.register(EmployeeResource)
    registry.register_authz()
    store = InMemoryStore.from_registry(registry)
    config = AuthzConfig(
        role_source=IdpRoleSource({"hr": "hr", "staff": "staff", "viewers": "viewer"}),
        bootstrap={"platform-admin": "admin"},
    )
    authz = PolicyAuthorizer(config, store.unit_of_work)
    manager = ResourceManager(registry, store.unit_of_work, authorizer=authz)
    for name in ("hr", "staff"):
        await manager.create("authz_roles", {"name": name}, actor=ADMIN)
    system = system_principal()
    await manager.create(
        "employees", {"name": "Ada", "team": "eng", "salary": 100, "notes": "n1"}, actor=system
    )
    await manager.create(
        "employees", {"name": "Bob", "team": "ops", "salary": 90, "notes": "n2"}, actor=system
    )

    async def policy(name, effect, roles, actions, condition=None):
        await manager.create(
            "authz_policies",
            {
                "name": name,
                "effect": effect,
                "subjects": {"roles": roles},
                "actions": actions,
                "condition": condition,
            },
            actor=ADMIN,
        )

    await policy("Everybody reads and edits", "allow", ["hr", "staff"], ["employees:*"])
    await policy("HR handles salaries", "allow", ["hr"], ["employees:read:salary"])
    return manager, authz, policy


async def test_field_actions_in_the_catalogue(fields_env):
    _, authz, _ = fields_env
    reads = authz.catalog.field_actions("employees", "read")
    writes = authz.catalog.field_actions("employees", "write")
    assert set(reads) == {"name", "team", "salary", "notes"}  # not the id
    assert set(writes) == {"name", "team", "salary", "notes"}
    assert reads["salary"].restricted and not reads["name"].restricted
    perms = await authz.permissions(ADMIN)
    assert "employees:read:salary" not in perms  # fields are reported separately


async def test_restricted_fields_are_omitted(fields_env):
    manager, _, _ = fields_env
    staff_view = await manager.get("employees", 1, actor=STAFF)
    assert staff_view.model_dump() == {"id": 1, "name": "Ada", "team": "eng", "notes": "n1"}
    with pytest.raises(AttributeError):
        _ = staff_view.salary
    assert (await manager.get("employees", 1, actor=HR)).salary == 100
    assert (await manager.get("employees", 1, actor=ADMIN)).salary == 100  # system role
    viewer = principal("vera", "viewers")
    assert "salary" not in (await manager.get("employees", 1, actor=viewer)).model_dump()
    page = await manager.list("employees", actor=STAFF)
    assert all("salary" not in e.model_dump() for e in page.items)


async def test_writing_protected_fields_is_rejected(fields_env):
    manager, _, _ = fields_env
    with pytest.raises(FieldNotWritable) as info:
        await manager.update("employees", 1, {"salary": 1}, actor=STAFF)
    assert info.value.details["fields"] == ["salary"]
    assert (await manager.get("employees", 1, actor=ADMIN)).salary == 100  # nothing saved
    with pytest.raises(FieldNotWritable):
        await manager.create(
            "employees", {"name": "C", "team": "eng", "salary": 1}, actor=HR
        )  # HR may read salaries, not write them
    updated = await manager.update("employees", 1, {"team": "core"}, actor=STAFF)
    assert updated.team == "core" and "salary" not in updated.model_dump()


async def test_filtering_or_sorting_by_hidden_fields_is_refused(fields_env):
    manager, _, _ = fields_env
    with pytest.raises(FieldNotReadable):
        await manager.list("employees", ListQuery(filters={"salary": 100}), actor=STAFF)
    with pytest.raises(FieldNotReadable):
        await manager.list("employees", ListQuery(order_by=["-salary"]), actor=STAFF)
    page = await manager.list("employees", ListQuery(order_by=["-salary"]), actor=HR)
    assert [e.name for e in page.items] == ["Ada", "Bob"]


async def test_policies_naming_a_field_restrict_it(fields_env):
    manager, authz, policy = fields_env
    assert "notes" in (await manager.get("employees", 1, actor=STAFF)).model_dump()
    await policy("HR keeps notes", "allow", ["hr"], ["employees:*:notes"])
    assert "notes" not in (await manager.get("employees", 1, actor=STAFF)).model_dump()
    assert (await manager.get("employees", 1, actor=HR)).notes == "n1"
    access = await authz.field_permissions(STAFF)
    assert access["employees"]["hidden"] == ["notes", "salary"]
    assert access["employees"]["readonly"] == ["notes", "salary"]


async def test_wildcards_do_not_restrict_fields(fields_env):
    manager, _, policy = fields_env
    await policy("HR reads everything", "allow", ["hr"], ["employees:read:*"])
    assert "notes" in (await manager.get("employees", 1, actor=STAFF)).model_dump()


async def test_conditional_field_denies_apply_per_record(fields_env):
    manager, _, _ = fields_env
    await manager.create(
        "authz_policies",
        {
            "name": "Ops notes are private",
            "effect": "deny",
            "subjects": {"everyone": True},
            "actions": ["employees:read:notes"],
            "condition": {"eq": [{"attr": "resource.team"}, "ops"]},
        },
        actor=ADMIN,
    )
    page = await manager.list("employees", ListQuery(order_by=["id"]), actor=STAFF)
    assert [("notes" in e.model_dump()) for e in page.items] == [True, False]
    assert (await manager.get("employees", 2, actor=ADMIN)).notes == "n2"  # system role wins


async def test_granting_fields_needs_holding_them(fields_env):
    manager, _, _ = fields_env
    editor = principal("ed", "editors")
    manager.authorizer.role_source = IdpRoleSource({"editors": "editor"})
    await manager.create("authz_roles", {"name": "editor"}, actor=ADMIN)
    await manager.create(
        "authz_policies",
        {
            "name": "Editors manage policies",
            "effect": "allow",
            "subjects": {"roles": ["editor"]},
            "actions": ["authz_policies:create", "employees:read:name"],
        },
        actor=ADMIN,
    )
    with pytest.raises(PrivilegeEscalation):
        await manager.create(
            "authz_policies",
            {
                "name": "Grant salaries",
                "effect": "allow",
                "subjects": {"roles": ["editor"]},
                "actions": ["employees:read:salary"],
            },
            actor=editor,
        )


def test_restricted_fields_must_exist():
    class Broken(EmployeeResource):
        name = "broken"
        restricted_fields = ["wage"]

    with pytest.raises(ConfigurationError):
        Broken.definition()


async def test_broad_grants_need_no_restricted_fields(fields_env):
    manager, authz, _ = fields_env
    authz.role_source = IdpRoleSource({"editors": "editor"})
    await manager.create("authz_roles", {"name": "editor"}, actor=ADMIN)
    await manager.create(
        "authz_policies",
        {
            "name": "Editors handle employees",
            "effect": "allow",
            "subjects": {"roles": ["editor"]},
            "actions": ["employees:*", "authz_policies:create"],
        },
        actor=ADMIN,
    )
    editor = principal("ed", "editors")
    await manager.create(  # employees:* does not grant salary, so it is no escalation
        "authz_policies",
        {
            "name": "Same for staff",
            "effect": "allow",
            "subjects": {"roles": ["staff"]},
            "actions": ["employees:*"],
        },
        actor=editor,
    )
    decision = await authz.check(editor, "employees:read:salary")
    assert decision.allowed is False
