"""Composition root of the example application."""

from __future__ import annotations

from resource_engine import ResourceRegistry
from resource_engine.authz import AuthzConfig, IdpRoleSource, SystemRole

from invoices.application.customers import CustomerResource
from invoices.application.resource import InvoiceResource
from invoices.application.tags import InvoiceTagResource, TagResource

registry = ResourceRegistry()
registry.register(CustomerResource)
registry.register(InvoiceResource)
registry.register(TagResource)
registry.register(InvoiceTagResource)
registry.register_documents()
registry.register_authz()
registry.register_audit()

# Permissions: system roles are fixed here; everything else is policies created
# from the front end (see invoices.authz for the seed used by tests and demos).
authz = AuthzConfig(
    system_roles=[SystemRole.admin(), SystemRole.viewer()],
    role_source=IdpRoleSource(
        {
            "invoices-admin": "admin",
            "accountant": "viewer",
            "approver": "approver",
            "issuer": "issuer",
        }
    ),
    bootstrap={"platform-admin": "admin"},
)
