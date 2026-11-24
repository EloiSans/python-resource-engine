"""Composition root of the help desk."""

from __future__ import annotations

from helpdesk.customers.application.resource import CustomerResource
from helpdesk.tickets.application.resource import TicketResource
from resource_engine import ResourceRegistry
from resource_engine.authz import AuthzConfig, IdpRoleSource, SystemRole

registry = ResourceRegistry()
registry.register(CustomerResource)
registry.register(TicketResource)
registry.register_authz()

authz = AuthzConfig(
    system_roles=[SystemRole.admin(), SystemRole.viewer()],
    role_source=IdpRoleSource(
        {"helpdesk-admin": "admin", "agent": "agent", "supervisor": "supervisor"}
    ),
)
