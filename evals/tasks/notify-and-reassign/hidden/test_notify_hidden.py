"""Hidden checks for the notify-and-reassign task."""

from __future__ import annotations

import pytest
from helpdesk.authz import seed
from helpdesk.bootstrap import authz, registry
from resource_engine import ResourceManager
from resource_engine.authz import PolicyAuthorizer
from resource_engine.errors import ValidationFailed
from resource_engine.ports.identity import Principal
from resource_engine.testing import InMemoryStore

pytestmark = pytest.mark.asyncio

ADMIN = Principal(subject="root", issuer="idp", roles=frozenset({"helpdesk-admin"}))
AGENT = Principal(subject="ana", issuer="idp", roles=frozenset({"agent"}))


class Mailer:
    def __init__(self) -> None:
        self.sent: list[tuple] = []

    async def send(self, customer_id, subject, body):
        self.sent.append((customer_id, subject, body))


class FailingCommit:
    """Unit of work whose commit fails when ``fail`` is set."""

    def __init__(self, inner, flag):
        self.inner, self.flag = inner, flag

    async def __aenter__(self):
        await self.inner.__aenter__()
        return self

    async def __aexit__(self, *exc):
        return await self.inner.__aexit__(*exc)

    async def commit(self):
        if self.flag["fail"]:
            raise RuntimeError("database down")
        await self.inner.commit()

    def __getattr__(self, name):
        return getattr(self.inner, name)


@pytest.fixture
async def setup():
    store = InMemoryStore.from_registry(registry)
    flag = {"fail": False}
    factory = lambda: FailingCommit(store.unit_of_work(), flag)  # noqa: E731
    mailer = Mailer()
    manager = ResourceManager(
        registry,
        factory,
        authorizer=PolicyAuthorizer(authz, store.unit_of_work),
        services={"mailer": mailer},
    )
    await seed(manager, ADMIN)
    return manager, mailer, flag


async def started(manager):
    ticket = await manager.create("tickets", {"customer_id": 42, "subject": "VPN"}, actor=AGENT)
    await manager.transition("tickets", ticket.id, "start", actor=AGENT)
    return ticket.id


async def test_resolution_emails_the_customer_once(setup):
    manager, mailer, _ = setup
    ticket_id = await started(manager)
    assert mailer.sent == []
    await manager.transition("tickets", ticket_id, "resolve", actor=AGENT)
    assert len(mailer.sent) == 1 and mailer.sent[0][0] == 42


async def test_no_email_when_the_commit_fails(setup):
    manager, mailer, flag = setup
    ticket_id = await started(manager)
    flag["fail"] = True
    with pytest.raises(RuntimeError):
        await manager.transition("tickets", ticket_id, "resolve", actor=AGENT)
    assert mailer.sent == []


async def test_no_email_for_other_transitions(setup):
    manager, mailer, _ = setup
    await started(manager)
    assert mailer.sent == []


async def test_agents_reassign(setup):
    manager, _, _ = setup
    ticket = await manager.create("tickets", {"customer_id": 1, "subject": "Mail"}, actor=AGENT)
    assert ticket.assignee is None
    ticket = await manager.run_action(
        "tickets", "reassign", ticket.id, {"assignee": "joan"}, actor=AGENT
    )
    assert ticket.assignee == "joan"
    assert (await manager.get("tickets", ticket.id, actor=AGENT)).assignee == "joan"
    with pytest.raises(ValidationFailed):
        await manager.run_action("tickets", "reassign", ticket.id, {"assignee": ""}, actor=AGENT)
